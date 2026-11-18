"""Identify tabular files from their contents before admitting them to a study."""

import csv
import stat
import zipfile
from pathlib import Path, PurePosixPath

import pandas as pd

LIMIT = 512 * 1024 * 1024
FORMATS = {".csv", ".tsv", ".dta", ".sav", ".rdata", ".rda", ".rds"}
csv.field_size_limit(16 * 1024 * 1024)


def copy_limited(src, dst, limit=LIMIT):
    n = 0
    with dst.open("wb") as out:
        while chunk := src.read(1024 * 1024):
            n += len(chunk)
            if n > limit:
                raise ValueError("파일 크기가 수집기의 512 MiB 제한을 초과합니다.")
            out.write(chunk)
    if n == 0:
        raise ValueError("빈 파일입니다.")


def unpack(raw, dest, member=None):
    """Read one explicit member. No archive-provided path becomes an output path."""
    if raw.suffix.lower() != ".zip":
        return raw
    with zipfile.ZipFile(raw) as z:
        infos = z.infolist()
        if len(infos) > 10000 or sum(v.file_size for v in infos) > LIMIT:
            raise ValueError("압축 해제 크기 또는 파일 수 초과")
        candidates = []
        names = set()
        for v in infos:
            p = PurePosixPath(v.filename)
            if (
                p.is_absolute()
                or ".." in p.parts
                or "\\" in v.filename
                or ":" in v.filename
                or stat.S_ISLNK(v.external_attr >> 16)
                or v.flag_bits & 1
                or v.filename in names
            ):
                raise ValueError("안전하게 해제할 수 없는 ZIP 항목입니다.")
            names.add(v.filename)
            if (
                p.suffix.lower() in FORMATS
                and not v.is_dir()
                and "__MACOSX" not in p.parts
            ):
                if member is None or v.filename == member:
                    candidates.append(v)
        if len(candidates) != 1:
            raise ValueError("ZIP의 데이터 파일 한 개를 --member로 지정하세요.")
        info = candidates[0]
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / ("table" + Path(info.filename).suffix.lower())
        with z.open(info) as src:
            copy_limited(src, target)
        return target


def load_frame(path, options=None):
    opt = options or {}
    with path.open("rb") as f:
        sample = f.read(12000)
    prefix = sample.lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    if not prefix or prefix.startswith(
        (b"<html", b"<!doctype", b"<?xml", b"<head", b"<body", b"%pdf", b'{"error"')
    ):
        raise ValueError("응답이 데이터 파일 형식과 일치하지 않습니다.")
    ext = path.suffix.lower()
    if ext in (".dta", ".sav"):
        try:
            import pyreadstat
        except ImportError as ex:
            raise ValueError(
                "라벨 자료 점검에 pyreadstat가 필요합니다. python -m pip install -r requirements.txt"
            ) from ex
        reader = pyreadstat.read_dta if ext == ".dta" else pyreadstat.read_sav
        frame, m = reader(
            str(path), user_missing=True, disable_datetime_conversion=True
        )
        details = dict(
            encoding=m.file_encoding,
            delimiter=None,
            labels=m.column_names_to_labels,
            value_labels=m.variable_value_labels,
            missing_ranges=m.missing_ranges,
            missing_values=m.missing_user_values,
            native_formats=m.original_variable_types,
            source_header=m.column_names,
        )
    elif ext in (".rdata", ".rda", ".rds"):
        import pyreadr

        objects = pyreadr.read_r(str(path))
        if len(objects) != 1:
            raise ValueError("표 하나를 담은 RData 또는 RDS 파일이 필요합니다.")
        name, frame = next(iter(objects.items()))
        if opt.get("object") and name != opt["object"]:
            raise ValueError("배포판 R 객체 이름이 다릅니다.")
        details = dict(
            encoding=None,
            delimiter=None,
            labels={},
            value_labels={
                k: [str(x) for x in v.cat.categories]
                for k, v in frame.items()
                if isinstance(v.dtype, pd.CategoricalDtype)
            },
            missing_ranges={},
            missing_values={},
            native_formats={},
            source_header=list(frame.columns),
            object_name=name,
            note="R 객체의 전체 속성·라벨·날짜 정보는 R 가져오기 파일에 보존됩니다.",
        )
    elif ext in (".csv", ".tsv"):
        if b"\x00" in sample:
            raise ValueError("본문에 이진 데이터가 있습니다.")
        enc = opt.get("encoding") or "utf-8-sig"
        sep = opt.get("delimiter") or ("\t" if ext == ".tsv" else ",")
        if sep not in (",", "\t", ";", "|"):
            raise ValueError("지원하지 않는 구분자입니다.")
        with path.open(encoding=enc, newline="") as f:
            parser = csv.reader(f, delimiter=sep, strict=True)
            header = next(parser, [])
            if header:
                header[0] = header[0].lstrip("\ufeff")
            names = [c or "source_row" for c in header]
            if (
                len(names) < 2
                or len(set(names)) != len(names)
                or any("\n" in c for c in names)
            ):
                raise ValueError("열 헤더·구분자·인코딩을 확인하세요.")
            expected = opt.get("header")
            if expected and names != expected:
                raise ValueError("고정 배포판의 헤더와 일치하지 않습니다.")
            for line in parser:
                if line and len(line) != len(names):
                    raise ValueError(
                        f"{parser.line_num}행의 필드 수가 헤더와 다릅니다."
                    )
        frame = pd.read_csv(
            path,
            encoding=enc,
            sep=sep,
            names=names,
            header=0,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
        )
        details = dict(
            encoding=enc,
            delimiter=sep,
            source_header=header,
            labels={},
            value_labels={},
            missing_ranges={},
            missing_values={},
            native_formats={},
        )
    else:
        raise ValueError(
            "지원 형식은 CSV·TSV·DTA·SAV·RData·RDS 및 이 파일을 담은 ZIP입니다."
        )
    if frame.empty:
        raise ValueError("관측치가 없습니다.")
    return frame, details


def missing_mask(series, name, meta, tokens):
    mask = (
        series.isna()
        | series.isin(tokens)
        | series.isin(meta["missing_values"].get(name, []))
    )
    for ran in meta["missing_ranges"].get(name, []):
        mask |= series.between(ran["lo"], ran["hi"]).fillna(False)
    return mask


def profile(frame, meta, keys, tokens):
    cols = {}
    for name, series in frame.items():
        mask = missing_mask(series, name, meta, tokens)
        vals = series[~mask]
        inferred = pd.api.types.infer_dtype(vals, skipna=True) if len(vals) else "empty"
        cols[name] = {
            "storage": str(series.dtype),
            "content": inferred,
            "missing": int(mask.sum()),
            "distinct": int(vals.nunique()),
            "label": meta["labels"].get(name),
        }
    missing_keys = sorted(set(keys) - set(cols))
    counts = None
    if keys and not missing_keys:
        counts = {
            "missing_rows": int(
                pd.concat(
                    [missing_mask(frame[k], k, meta, tokens) for k in keys], axis=1
                )
                .any(axis=1)
                .sum()
            ),
            "duplicate_rows": int(frame.duplicated(keys, keep=False).sum()),
            "extra_rows": int(frame.duplicated(keys).sum()),
        }
    return {
        "rows": len(frame),
        "column_count": len(cols),
        "columns": cols,
        "format_metadata": meta,
        "keys": keys,
        "missing_keys": missing_keys,
        "key_check": counts,
    }
