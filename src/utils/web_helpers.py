"""Helper utilities for the extranet MCP server."""

import json
import logging
import os
import re
from functools import wraps
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


class WebRequestError(Exception):
    """Custom exception for web request errors."""

    pass


# Configuration with fallback defaults
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
MAX_PAGE_LENGTH = int(os.getenv("MAX_PAGE_LENGTH", "10000"))
MAX_REDIRECTS = int(os.getenv("MAX_REDIRECTS", "5"))
MAX_CONTENT_BYTES = int(os.getenv("MAX_CONTENT_BYTES", "10485760"))  # 10MB

# Unsafe URL schemes to filter out
UNSAFE_SCHEMES = (
    "javascript:",
    "vbscript:",
    "data:",
    "file:",
    "blob:",
    "mailto:",
    "tel:",
    "ws:",
    "wss:",
    "ftp:",
)

# HTTP status code messages
HTTP_STATUS_MESSAGES: Dict[int, str] = {
    401: "Authentication required",
    403: "Access denied",
    404: "Page not found",
    500: "Server error",
    502: "Bad gateway",
    503: "Service unavailable",
    504: "Gateway timeout",
}

# Default headers for HTTP requests
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": "Mozilla/5.0 (compatible; ExtranetMCP/1.0)",
}

# Connection limits for HTTP client
CONNECTION_LIMITS = httpx.Limits(max_keepalive_connections=10, max_connections=20)

# HTML elements to remove when cleaning content
REMOVABLE_HTML_ELEMENTS = ["script", "style", "meta", "noscript"]


def extract_error_message(response: httpx.Response) -> str:
    """
    Extract a meaningful error message from HTTP response.

    Args:
        response: The HTTP response object

    Returns:
        A cleaned error message string
    """
    try:
        # Try to get JSON error message
        error_data = response.json()
        if isinstance(error_data, dict):
            # Common error message fields
            for field in ["message", "error", "detail", "description"]:
                if field in error_data:
                    return str(error_data[field])
        return str(error_data)
    except (ValueError, TypeError, AttributeError) as e:
        # Fall back to text content if JSON parsing fails
        logger.debug(f"Failed to parse JSON error response: {e}")
        return _extract_text_error(response)


def _extract_text_error(response: httpx.Response) -> str:
    """Extract error message from text/HTML response."""
    text = response.text.strip()
    if not text:
        return "Unknown error"

    # If it's HTML, try to extract meaningful content
    if "text/html" in response.headers.get("content-type", ""):
        try:
            soup = BeautifulSoup(text, "html.parser")
            # Look for common error elements
            for selector in ["h1", "h2", "title", ".error", "#error"]:
                error_elem = soup.find(selector)
                if error_elem:
                    return _truncate_text(error_elem.get_text().strip())

            # Fall back to body text
            body = soup.find("body")
            if body:
                return _truncate_text(body.get_text().strip())
        except (ValueError, TypeError, AttributeError) as e:
            # If HTML parsing fails, continue to fallback
            logger.debug(f"Failed to parse HTML error response: {e}")

    # Return first line of text response
    first_line = text.split("\n")[0]
    return _truncate_text(first_line)


def _truncate_text(text: str, max_length: int = 200) -> str:
    """Truncate text to maximum length."""
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}..."


def handle_web_request_errors(func):
    """
    Decorator for handling web request errors with intelligent messages.

    This decorator catches various HTTP errors and converts them to
    meaningful error messages.
    """

    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            url = str(e.request.url) if e.request else "unknown URL"
            status_code = e.response.status_code

            # Check for known status codes
            if status_code in HTTP_STATUS_MESSAGES:
                raise WebRequestError(f"{HTTP_STATUS_MESSAGES[status_code]}: {url}")

            # Extract meaningful error message from response
            error_msg = extract_error_message(e.response)
            raise WebRequestError(f"HTTP {status_code}: {error_msg} (URL: {url})")

        except httpx.TimeoutException:
            url = _extract_url_from_args(args)
            raise WebRequestError(f"Request timed out: {url}")
        except httpx.ConnectError:
            url = _extract_url_from_args(args)
            raise WebRequestError(f"Connection failed: {url}")
        except httpx.RequestError as e:
            url = _extract_url_from_args(args)
            raise WebRequestError(f"Request failed: {str(e)} (URL: {url})")

    return wrapper


def _extract_url_from_args(args: tuple) -> str:
    """Extract URL from function arguments."""
    return args[0] if args else "unknown URL"


def validate_url(url: str) -> str:
    """
    Validate URL format (simplified - no network restrictions).

    Args:
        url: The URL to validate

    Returns:
        The validated URL

    Raises:
        WebRequestError: If the URL is invalid
    """
    if not url or not url.strip():
        raise WebRequestError("URL is required and cannot be empty")

    try:
        parsed = urlparse(url)
    except Exception as e:
        raise WebRequestError(f"Invalid URL format: {e}")

    # Require HTTP/HTTPS schemes
    if parsed.scheme not in ("http", "https"):
        raise WebRequestError("URL must start with http:// or https://")

    # Require hostname
    if not parsed.hostname:
        raise WebRequestError("URL must have a hostname")

    return url


async def safe_fetch(url: str, timeout: int = REQUEST_TIMEOUT) -> httpx.Response:
    """
    Fetch URL with redirect limits, TLS verify, and size cap.

    Args:
        url: The URL to fetch
        timeout: Request timeout in seconds

    Returns:
        HTTP response with content loaded and size-checked

    Raises:
        WebRequestError: If request fails or violates constraints
    """
    url = validate_url(url)

    # Use separate connect/read timeouts
    timeout_config = httpx.Timeout(timeout, connect=timeout, read=timeout)

    async with httpx.AsyncClient(
        limits=CONNECTION_LIMITS,
        headers=DEFAULT_HEADERS,
        follow_redirects=False,
        verify=True,
    ) as client:
        redirects = 0
        current_url = url

        while True:
            try:
                resp = await client.get(current_url, timeout=timeout_config)
            except httpx.HTTPStatusError as e:
                raise WebRequestError(f"HTTP {e.response.status_code}: {current_url}")
            except httpx.TimeoutException:
                raise WebRequestError(f"Request timed out: {current_url}")
            except httpx.ConnectError:
                raise WebRequestError(f"Connection failed: {current_url}")
            except httpx.RequestError as e:
                raise WebRequestError(f"Request failed: {str(e)} (URL: {current_url})")

            # Handle redirects
            if (
                hasattr(resp, "status_code")
                and 300 <= resp.status_code < 400
                and "location" in resp.headers
            ):
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    raise WebRequestError(f"Too many redirects: {redirects}")

                next_url = urljoin(current_url, resp.headers["location"])
                current_url = validate_url(next_url)
                continue

            # Raise for non-2xx status codes
            resp.raise_for_status()

            # Enforce size cap as we read
            if hasattr(resp, "aiter_bytes"):
                total_bytes = 0
                chunks = []

                async for chunk in resp.aiter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > MAX_CONTENT_BYTES:
                        raise WebRequestError(
                            f"Response too large (> {MAX_CONTENT_BYTES} bytes)"
                        )
                    chunks.append(chunk)

                # Finalize buffered content
                resp._content = b"".join(chunks)

            return resp


def truncate_content(content: str, max_length: int) -> str:
    """
    Truncate content to stay within max_length, keeping beginning and end.

    Args:
        content: The content to truncate
        max_length: Maximum allowed length

    Returns:
        Truncated content with indicator in the middle if truncated
    """
    if len(content) <= max_length:
        return content

    half_length = max_length // 2
    truncation_msg = f"\n..._This content has been truncated to stay below {max_length} characters_...\n"

    return content[:half_length] + truncation_msg + content[-half_length:]


def clean_html_content(html_text: str) -> str:
    """
    Clean HTML content by removing scripts, styles, and other non-content elements.

    Args:
        html_text: Raw HTML text

    Returns:
        Cleaned HTML string
    """
    soup = BeautifulSoup(html_text, "html.parser")

    # Remove non-content elements
    for element in soup(REMOVABLE_HTML_ELEMENTS):
        element.decompose()

    return str(soup).strip()


def clean_whitespace(content: str) -> str:
    """Clean up excessive newlines and whitespace."""
    # Replace multiple newlines with single newline
    content = re.sub(r"\n{2,}", "\n", content)
    # Remove trailing whitespace from lines
    content = "\n".join(line.rstrip() for line in content.split("\n"))
    return content.strip()


def find_line_number(content: str, position: int) -> int:
    """
    Find the line number for a given position in content.

    Args:
        content: The full text content
        position: The character position

    Returns:
        Line number (1-based)
    """
    return content[:position].count("\n") + 1


def extract_context(content: str, match_start: int, match_end: int, window: int) -> str:
    """
    Extract context around a match.

    Args:
        content: The full text content
        match_start: Start position of the match
        match_end: End position of the match
        window: Number of characters to include before/after

    Returns:
        Context string with the match and surrounding text
    """
    # Calculate context boundaries
    context_start = max(0, match_start - window)
    context_end = min(len(content), match_end + window)

    # Extract context
    context = content[context_start:context_end]

    # Clean up context edges to avoid cutting words
    if context_start > 0:
        # Find first space or newline after context_start
        first_break = context.find(" ")
        first_newline = context.find("\n")
        if first_break > 0 or first_newline > 0:
            if first_break < 0:
                first_break = first_newline
            elif first_newline >= 0:
                first_break = min(first_break, first_newline)
            context = context[first_break:].lstrip()

    if context_end < len(content):
        # Find last space or newline before context_end
        last_break = context.rfind(" ")
        last_newline = context.rfind("\n")
        if last_break > 0 or last_newline > 0:
            if last_break < 0:
                last_break = last_newline
            else:
                last_break = max(last_break, last_newline)
            context = context[: last_break + 1].rstrip()

    return context


def search_content(
    content: str,
    pattern: str,
    case_sensitive: bool,
    max_matches: int,
) -> List[Dict[str, Any]]:
    """Simple string search function."""

    # For case-insensitive search, convert both content and pattern to lowercase
    if case_sensitive:
        haystack = content
        needle = pattern
    else:
        haystack = content.casefold()
        needle = pattern.casefold()

    matches = []
    start_pos = 0

    while len(matches) < max_matches:
        # Find next occurrence
        pos = haystack.find(needle, start_pos)
        if pos == -1:
            break

        # Add match info (using original content for the match text)
        matches.append(
            {
                "match": content[pos : pos + len(pattern)],
                "start": pos,
                "end": pos + len(pattern),
            }
        )

        # Move start position for next search
        start_pos = pos + 1

    return matches


def extract_text_from_html(
    html_content: str, content_type: str, clean_html: bool = True
) -> str:
    """
    Extract clean text content from HTML or return raw content.

    Args:
        html_content: Raw HTML or text content
        content_type: Content-Type header value
        clean_html: Whether to clean HTML elements (default=True)

    Returns:
        Clean text content suitable for searching
    """
    if clean_html:
        # Check if content is HTML
        if "text/html" in content_type.casefold():
            # Clean HTML and extract text
            soup = BeautifulSoup(html_content, "html.parser")
            # Remove non-content elements
            for element in soup(REMOVABLE_HTML_ELEMENTS):
                element.decompose()
            # Get text content with normalized spacing
            return soup.get_text(separator=" ", strip=True)
        else:
            # For non-HTML content, use as-is
            return html_content
    else:
        return html_content


def extract_urls_from_html(html: str) -> List[str]:
    """
    Extract URLs from HTML using BeautifulSoup.

    Args:
        html: HTML string to parse and extract URLs from

    Returns:
        List of unique URLs found (duplicates automatically removed)
    """
    soup = BeautifulSoup(html, "html.parser")
    urls = set()

    # Find all elements with href attributes
    for element in soup.find_all(href=True):
        if isinstance(element, Tag):
            url = element.get("href")
            if url:
                urls.add(url)

    # Find all elements with src attributes
    for element in soup.find_all(src=True):
        if isinstance(element, Tag):
            url = element.get("src")
            if url:
                urls.add(url)

    # Find all elements with action attributes
    for element in soup.find_all(action=True):
        if isinstance(element, Tag):
            url = element.get("action")
            if url:
                urls.add(url)

    # Data attributes
    for attr in ["data-url", "data-href", "data-src"]:
        for element in soup.find_all(attrs={attr: True}):
            if isinstance(element, Tag):
                url = element.get(attr)
                if url:
                    urls.add(url)

    return list(urls)


def parse_json_response(response: httpx.Response) -> Any:
    """Attempt to parse response as JSON, fallback to text if not valid JSON."""
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return response.text


def prepare_headers(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Prepare headers for the request, ensuring proper format."""
    if headers is None:
        return {}

    # Ensure all header values are strings
    return {k: str(v) for k, v in headers.items()}


async def make_http_request(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Any] = None,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Make an HTTP request using the specified method.

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH)
        url: The URL to send the request to
        headers: Optional dictionary of HTTP headers to include
        body: Optional request body (for non-JSON data)
        json_data: Optional JSON data to send
        params: Optional dictionary of query parameters
        timeout: Request timeout in seconds

    Returns:
        Dictionary containing status_code, headers, body, and url of the response
    """
    # Validate URL
    url = validate_url(url)

    # Prepare request parameters
    headers = prepare_headers(headers)

    # Build request kwargs
    request_kwargs = {"headers": headers}

    if params:
        request_kwargs["params"] = params

    if json_data is not None:
        request_kwargs["json"] = json_data
    elif body is not None:
        request_kwargs["content"] = body

    # Use configured timeout if not specified
    timeout_value = timeout if timeout is not None else REQUEST_TIMEOUT

    async with httpx.AsyncClient(timeout=timeout_value) as client:
        http_method = getattr(client, method.lower())
        response = await http_method(url, **request_kwargs)

        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": parse_json_response(response),
            "url": str(response.url),
        }
