import csv
import io


def decode_csv(content: bytes) -> tuple[str, list[dict[str, str]]]:
    text = content.decode("utf-8-sig")
    sample = text[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=";,").delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample.splitlines() else ""
        delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    if not reader.fieldnames:
        return delimiter, []
    rows = []
    for row in reader:
        if row is None or not any((value or "").strip() for value in row.values()):
            continue
        rows.append(
            {
                str(key).strip(): (value or "").strip()
                for key, value in row.items()
                if key is not None
            }
        )
    return delimiter, rows
