"""기존 상세페이지 URL 에서 텍스트와 상세 이미지를 가져온다.

원칙
- 셀러 본인의 상품 페이지를 가져오는 용도다. 사이트의 robots.txt 가 막으면 가져오지 않는다.
- 서버가 임의의 주소에 접속하는 기능이므로, 내부망·로컬 주소 접속을 막는다(SSRF 방지).
- 쿠팡·스마트스토어처럼 봇 차단이나 자바스크립트로 화면을 그리는 쇼핑몰은 가져올 수 없다.
  이 경우 'PDF 로 저장 후 업로드' 같은 대안을 안내한다(브라우저 자동화 도구는 쓰지 않는다).
"""
import hashlib
import io
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from PIL import Image

USER_AGENT = "DetailPageLocalizer/0.2 (seller-requested import of own product page)"
TIMEOUT = 10
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CANDIDATES = 40       # 페이지에서 살펴볼 이미지 후보 수
MAX_TEXT_CHARS = 15000
MIN_IMAGE_WIDTH = 400     # 아이콘·로고·버튼은 제외
MIN_IMAGE_HEIGHT = 300
SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-lazy", "ec-data-src", "src")

FALLBACK_HELP = (
    "대안: ① 브라우저에서 상세페이지를 열고 Ctrl+P → 'PDF로 저장' 후 PDF 탭에서 올리기, "
    "② 상세 이미지를 직접 저장해 이미지 탭에서 올리기."
)


class UrlImportError(Exception):
    """사용자에게 보여 줄 한국어 메시지를 담는 오류."""


@dataclass
class ImportedPage:
    url: str
    title: str
    text: str
    image_urls: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class FetchedImage:
    url: str
    data: bytes
    width: int
    height: int


# ── 안전한 요청 ──────────────────────────────────────────────
def _check_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UrlImportError("http:// 또는 https:// 로 시작하는 주소를 입력하세요.")
    if parsed.port not in (None, 80, 443):
        raise UrlImportError("일반 웹 주소(포트 80/443)만 가져올 수 있습니다.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise UrlImportError("주소를 찾을 수 없습니다. URL 을 확인하세요.")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise UrlImportError("내부망·로컬 주소는 가져올 수 없습니다.")


def safe_get(url: str, max_bytes: int) -> requests.Response:
    """리다이렉트마다 주소를 다시 검사하고, 크기 제한을 넘으면 중단한다."""
    for _ in range(4):
        _check_host(url)
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True, allow_redirects=False,
        )
        if response.is_redirect:
            url = urljoin(url, response.headers.get("location", ""))
            response.close()
            continue
        body = b""
        for chunk in response.iter_content(64 * 1024):
            body += chunk
            if len(body) > max_bytes:
                response.close()
                raise UrlImportError("파일이 너무 커서 가져올 수 없습니다.")
        response._content = body
        return response
    raise UrlImportError("리다이렉트가 너무 많습니다.")


def robots_allows(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = safe_get(robots_url, 512 * 1024)
    except (requests.RequestException, UrlImportError):
        return True       # robots.txt 를 받을 수 없으면 제한이 없는 것으로 본다
    if 400 <= response.status_code < 500:
        return True       # 표준: 4xx 면 제한 없음
    if response.status_code >= 500:
        return False      # 표준: 5xx 면 접근하지 않음
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


# ── HTML 해석 ────────────────────────────────────────────────
class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.texts: list[str] = []
        self.images: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        if tag == "meta" and attrs.get("property") == "og:title" and not self.title:
            self.title = attrs.get("content") or ""
        if tag == "img":
            src = next((attrs[a] for a in LAZY_ATTRS if attrs.get(a)), None)
            if not src and attrs.get("srcset"):
                src = attrs["srcset"].split(",")[-1].strip().split(" ")[0]
            if src and not src.startswith("data:"):
                self.images.append(src)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        elif not self.skip_depth and data.strip():
            self.texts.append(data.strip())


def _clean_text(chunks: list[str]) -> str:
    lines, seen = [], set()
    for chunk in chunks:
        line = re.sub(r"\s+", " ", chunk)
        if len(line) < 2 or line in seen:       # 메뉴·버튼처럼 반복되는 짧은 문구 제거
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)[:MAX_TEXT_CHARS]


def import_page(url: str) -> ImportedPage:
    url = url.strip()
    _check_host(url)
    if not robots_allows(url):
        raise UrlImportError(f"이 사이트는 자동 수집을 허용하지 않습니다(robots.txt). {FALLBACK_HELP}")
    try:
        response = safe_get(url, MAX_HTML_BYTES)
    except requests.RequestException:
        raise UrlImportError(f"페이지에 접속할 수 없습니다. {FALLBACK_HELP}")
    if response.status_code in (401, 403, 429, 503):
        raise UrlImportError(f"쇼핑몰이 자동 접속을 차단했습니다(HTTP {response.status_code}). {FALLBACK_HELP}")
    if response.status_code != 200:
        raise UrlImportError(f"페이지를 가져오지 못했습니다(HTTP {response.status_code}).")
    if "html" not in response.headers.get("content-type", ""):
        raise UrlImportError("웹 페이지(HTML) 주소가 아닙니다.")

    response.encoding = response.encoding or response.apparent_encoding
    parser = _PageParser()
    parser.feed(response.text)
    text = _clean_text(parser.texts)

    image_urls = []
    for src in parser.images:
        absolute = urljoin(response.url or url, src)
        if absolute not in image_urls:
            image_urls.append(absolute)
    page = ImportedPage(url=url, title=parser.title, text=text, image_urls=image_urls[:MAX_CANDIDATES])
    if len(text) < 200 and not image_urls:
        page.warnings.append(
            f"가져온 내용이 거의 없습니다. 자바스크립트로 화면을 그리는 쇼핑몰일 수 있습니다. {FALLBACK_HELP}"
        )
    if len(image_urls) > MAX_CANDIDATES:
        page.warnings.append(f"이미지가 많아 앞쪽 {MAX_CANDIDATES}개만 살펴봤습니다.")
    return page


def fetch_images(urls: list[str]) -> tuple[list[FetchedImage], int]:
    """상세 이미지로 보이는 것만 내려받는다. (가져온 이미지, 제외한 개수)."""
    fetched, skipped, hashes = [], 0, set()
    for url in urls:
        try:
            response = safe_get(url, MAX_IMAGE_BYTES)
            if response.status_code != 200:
                raise UrlImportError("응답 오류")
            digest = hashlib.sha1(response.content).hexdigest()
            with Image.open(io.BytesIO(response.content)) as img:
                width, height = img.size
        except Exception:   # 네트워크 오류, 이미지가 아닌 파일, 지나치게 큰 이미지 등
            skipped += 1
            continue
        if digest in hashes or width < MIN_IMAGE_WIDTH or height < MIN_IMAGE_HEIGHT:
            skipped += 1
            continue
        hashes.add(digest)
        fetched.append(FetchedImage(url, response.content, width, height))
    return fetched, skipped
