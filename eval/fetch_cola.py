"""Download approved labels from TTB's Public COLA Registry for evaluation.

    uv run python eval/fetch_cola.py            # every TTB ID in eval/real_labels.csv
    uv run python eval/fetch_cola.py 26257001000366 ...

Each COLA goes into its own folder, eval/real_labels/<ttb_id>/, holding the
label images and a meta.json with the application data the registry shows
(brand, fanciful name, class/type, origin, applicant). Contact names, phone
numbers and email addresses are deliberately not kept.

The images are public records but the artwork belongs to the brand owners,
so they are downloaded on demand and kept out of git (see .gitignore).

ttbonline.gov does not send its intermediate certificate, so the verified
intermediate is pinned in eval/certs/ and added to the normal system trust
store; TLS verification stays on.
"""

from __future__ import annotations

import csv
import html
import http.cookiejar
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "real_labels"
BASE = "https://www.ttbonline.gov/colasonline/"
INTERMEDIATE = HERE / "certs" / "entrust_ov_tls_issuing_rsa_ca2.pem"
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=INTERMEDIATE)
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), urllib.request.HTTPCookieProcessor(jar)
    )


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def field(page_text: str, label: str, next_label: str) -> str:
    match = re.search(re.escape(label) + r"\s*(.*?)\s*" + re.escape(next_label), page_text)
    return match.group(1).strip() if match else ""


def beverage_type(class_code: str) -> str:
    code = re.match(r"\d+", class_code or "")
    if not code:
        return "spirits"
    number = int(code.group())
    if 80 <= number <= 89:
        return "wine"
    if 900 <= number <= 999 and not class_code.startswith(("97", "98")):
        return "beer"
    return "spirits"


def safe_name(name: str) -> str:
    name = Path(name).name  # never let a server-supplied name escape the folder
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "label"


def fetch(ttb_id: str, web: urllib.request.OpenerDirector) -> dict:
    if not re.fullmatch(r"\d{14}", ttb_id):
        raise ValueError(f"not a TTB ID: {ttb_id!r}")
    get = lambda url: web.open(url, timeout=60).read()
    detail = text_of(get(f"{BASE}viewColaDetails.do?action=publicDisplaySearchBasic&ttbid={ttb_id}").decode("utf-8", "replace"))
    form_html = get(f"{BASE}viewColaDetails.do?action=publicFormDisplay&ttbid={ttb_id}").decode("utf-8", "replace")
    form = text_of(form_html)

    applicant = field(form, "INCLUDE APPROVED DBA OR TRADENAME IF USED ON LABEL (Required)", "4. SERIAL NUMBER")
    meta = {
        "ttb_id": ttb_id,
        "source": f"{BASE}viewColaDetails.do?action=publicDisplaySearchBasic&ttbid={ttb_id}",
        "status": field(detail, "Status:", "Vendor Code:"),
        "brand_name": field(detail, "Brand Name:", "Fanciful Name:"),
        "fanciful_name": field(detail, "Fanciful Name:", "Type of Application:"),
        "class_type_code": field(detail, "Class/Type Code:", "Origin Code:"),
        "origin": field(detail, "Origin Code:", "Brand Name:"),
        "approval_date": field(detail, "Approval Date:", "Qualifications:"),
        "applicant": applicant,
        "images": [],
    }
    meta["beverage_type"] = beverage_type(re.search(r"CT\s*(\S+)", form).group(1) if re.search(r"CT\s*(\S+)", form) else "")

    folder = OUT / ttb_id
    folder.mkdir(parents=True, exist_ok=True)
    sources = re.findall(r'src="(/colasonline/publicViewAttachment\.do\?[^"]+)"', form_html)
    for i, src in enumerate(sources):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(src)).query)
        name = safe_name(query.get("filename", [f"label_{i}"])[0])
        url = urllib.parse.urljoin(BASE, urllib.parse.quote(html.unescape(src), safe="/?=&"))
        data = web.open(url, timeout=60).read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES or not data:
            continue
        (folder / name).write_bytes(data)
        meta["images"].append(name)
    (folder / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main(ids: list[str]) -> None:
    if not ids:
        with open(HERE / "real_labels.csv", newline="", encoding="utf-8") as fh:
            ids = [row["ttb_id"] for row in csv.DictReader(fh)]
    web = opener()
    web.open(f"{BASE}publicSearchColasBasic.do", timeout=60).read()  # start a session
    for ttb_id in ids:
        try:
            meta = fetch(ttb_id, web)
            print(f"{ttb_id}  {meta['beverage_type']:7}  {meta['brand_name'][:30]:30}  {len(meta['images'])} image(s)")
        except Exception as exc:  # keep going; report at the end
            print(f"{ttb_id}  FAILED: {exc}", file=sys.stderr)
        time.sleep(0.5)  # be polite to a government server


if __name__ == "__main__":
    main(sys.argv[1:])
