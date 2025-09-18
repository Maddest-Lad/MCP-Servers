"""Extranet MCP Server - Standalone web search and HTTP requests server."""

import json
import logging
from typing import Any, Dict, Literal, Optional

from duckduckgo_search import DDGS
from fastmcp import FastMCP
from markdownify import markdownify

from ..utils.web_helpers import (
    MAX_PAGE_LENGTH,
    REQUEST_TIMEOUT,
    UNSAFE_SCHEMES,
    WebRequestError,
    clean_html_content,
    clean_whitespace,
    extract_context,
    extract_text_from_html,
    extract_urls_from_html,
    find_line_number,
    handle_web_request_errors,
    make_http_request,
    safe_fetch,
    search_content,
    truncate_content,
)

logger = logging.getLogger(__name__)

# HTTP method type
HttpMethod = Literal["GET", "POST", "PUT", "DELETE", "PATCH"]

# Create the FastMCP server
mcp = FastMCP("web")


@mcp.tool
async def search_web(query: str, max_results: int = 10) -> str:
    """
    Perform a web search based on your query (think a Google search) then returns the top search results.

    Args:
        query: The search query string.
        max_results: Maximum number of search results to return (1-100, default=10).

    Returns:
        Formatted search results with titles, URLs, and descriptions.

    Raises:
        ValueError: If max_results is out of valid range.
    """
    # Validate and bound the max_results parameter
    max_results = max(1, min(100, max_results))

    ddgs = DDGS(verify=False)
    results = ddgs.text(query, max_results=max_results)

    # Format results in a clean, readable way
    formatted_results = []
    for result in results:
        formatted_results.append(
            f"[{result['title']}]({result['href']})\n{result['body']}"
        )

    return "\n".join(formatted_results)


@mcp.tool
@handle_web_request_errors
async def visit_webpage(
    url: str,
    timeout: int = REQUEST_TIMEOUT,
    max_length: int = MAX_PAGE_LENGTH,
    convert_to_markdown: bool = False,
) -> str:
    """
    Visits a webpage at the given url and reads its content. Use this to browse webpages.

    Args:
        url: The url of the webpage to visit.
        timeout: The timeout in seconds for the request (default=30).
        max_length: The maximum number of characters of text that can be returned
                   (default=10000). If max_length==-1, text is not truncated and
                   the full webpage is returned.
        convert_to_markdown: Whether to convert HTML content to markdown format
                           (default=False). If False, returns clean HTML content
                           with scripts and styles removed.

    Returns:
        The webpage content, either as markdown or cleaned HTML.

    Raises:
        WebRequestError: If the request fails or times out.
    """
    response = await safe_fetch(url, timeout)

    if convert_to_markdown:
        content = markdownify(response.text).strip()
    else:
        # Check if content is HTML and clean it up
        content_type = response.headers.get("content-type", "").casefold()
        if "text/html" in content_type:
            content = clean_html_content(response.text)
        else:
            # For non-HTML content, return as-is
            content = response.text.strip()

    # Clean up whitespace
    content = clean_whitespace(content)

    # Apply truncation if needed
    if max_length == -1:
        return content
    return truncate_content(content, max_length)


@mcp.tool
@handle_web_request_errors
async def extract_urls_from_webpage(
    url: str,
    include_domains: bool = True,
    include_internal_only: bool = False,
    timeout: int = REQUEST_TIMEOUT,
) -> str:
    """
    Extract all URLs and domain names from a webpage, useful for link analysis,
    SEO auditing, and security assessment.

    Args:
        url: The webpage URL to analyze.
        include_domains: Whether to include a list of unique domains found (default=True).
        include_internal_only: Only return URLs from the same domain as the source (default=False).
        timeout: Request timeout in seconds (default=30).

    Returns:
        JSON string containing extracted URLs, domains, and statistics.

    Raises:
        WebRequestError: If the request fails or times out.
    """
    from urllib.parse import urljoin, urlparse

    response = await safe_fetch(url, timeout)

    content_type = response.headers.get("content-type", "").casefold()
    if "text/html" not in content_type:
        return f"Content-Type '{content_type}' is not HTML; skipping parse"

    # Extract URLs from HTML content
    raw_urls = extract_urls_from_html(response.text)

    # Parse the source URL to get domain info
    source_parsed = urlparse(url)
    source_domain = source_parsed.netloc.casefold()

    # Process URLs
    processed_urls = []
    domains = set()

    for raw_url in raw_urls:
        # Skip empty URLs
        if not raw_url or not raw_url.strip():
            continue

        # Skip unsafe URL schemes
        if raw_url.startswith(UNSAFE_SCHEMES):
            continue

        # Convert relative URLs to absolute
        absolute_url = urljoin(url, raw_url)
        parsed_url = urlparse(absolute_url)

        # Skip if no scheme (malformed URL)
        if not parsed_url.scheme:
            continue

        domain = parsed_url.netloc.casefold()

        # Apply internal_only filter
        if include_internal_only and domain != source_domain:
            continue

        processed_urls.append(absolute_url)
        if domain:
            domains.add(domain)

    # Count internal vs external URLs
    internal_count = sum(
        1 for u in processed_urls if urlparse(u).netloc.casefold() == source_domain
    )
    external_count = len(processed_urls) - internal_count

    # Build result
    result = {
        "source_url": url,
        "total_urls": len(processed_urls),
        "internal_urls": internal_count,
        "external_urls": external_count,
        "urls": processed_urls,
    }

    if include_domains:
        result["domains"] = sorted(domains)

    return json.dumps(result, indent=2)


@mcp.tool
@handle_web_request_errors
async def search_in_webpage(
    url: str,
    search_pattern: str,
    case_sensitive: bool = False,
    context_window: int = 100,
    max_matches: int = 10,
    timeout: int = REQUEST_TIMEOUT,
    clean_html: bool = True,
) -> str:
    """
    Search for specific content within a webpage using string patterns.

    Args:
        url: The webpage URL to search.
        search_pattern: String pattern to search for.
        case_sensitive: Whether search should be case sensitive (default=False).
        context_window: Characters to include before/after each match (default=100).
        max_matches: Maximum number of matches to return (1-100, default=10).
        timeout: Request timeout in seconds (default=30).
        clean_html: Whether to clean HTML before searching (default=True).

    Returns:
        JSON string containing search results with matches and context.

    Raises:
        WebRequestError: If the request fails or times out.
        ValueError: If parameters are invalid.
    """
    # Validate and bound parameters
    context_window = max(0, min(1000, context_window))
    max_matches = max(1, min(100, max_matches))

    response = await safe_fetch(url, timeout)

    # Get content to search using helper function
    content_type = response.headers.get("content-type", "")
    content = extract_text_from_html(response.text, content_type, clean_html)

    # Clean up whitespace
    content = clean_whitespace(content)

    # Search for matches
    matches = search_content(
        content=content,
        pattern=search_pattern,
        case_sensitive=case_sensitive,
        max_matches=max_matches,
    )

    # Build result with context and line numbers
    result_matches = []
    for match_info in matches:
        match_text = match_info["match"]
        start_pos = match_info["start"]
        end_pos = match_info["end"]

        # Extract context
        context = extract_context(content, start_pos, end_pos, context_window)

        # Find line number
        line_number = find_line_number(content, start_pos)

        result_matches.append(
            {
                "match": match_text,
                "context": context,
                "position": start_pos,
                "position_end": end_pos,
                "line_number": line_number,
            }
        )

    # Prepare final result
    result = {
        "url": url,
        "search_pattern": search_pattern,
        "case_sensitive": case_sensitive,
        "total_matches": len(result_matches),
        "matches": result_matches,
    }

    return json.dumps(result, indent=2)


@mcp.tool
async def http_request(
    method: HttpMethod,
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
        json_data: Optional JSON data to send (automatically sets Content-Type to application/json)
        params: Optional dictionary of query parameters (primarily for GET requests)
        timeout: Request timeout in seconds (uses configured default if not specified)

    Returns:
        Dictionary containing status_code, headers, body, and url of the response

    Note:
        - Use either 'body' for raw/form data or 'json_data' for JSON payloads, not both
        - Query parameters ('params') are typically used with GET requests

    Examples:
        # GET request with query parameters
        await http_request("GET", "https://api.example.com/users", params={"page": 1})

        # POST request with JSON data
        await http_request("POST", "https://api.example.com/users",
                          json_data={"name": "John", "email": "john@example.com"})

        # PUT request with raw body
        await http_request("PUT", "https://api.example.com/data",
                          body="raw text data", headers={"Content-Type": "text/plain"})
    """
    # Check for conflicting body parameters
    if json_data is not None and body is not None:
        raise ValueError(
            "Cannot specify both 'body' and 'json_data'. Use 'json_data' for JSON payloads or 'body' for other content types."
        )

    # Warn about unusual parameter usage (but don't block it)
    if params and method != "GET":
        logger.warning(
            f"Query parameters are typically used with GET requests, not {method}"
        )

    try:
        return await make_http_request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            json_data=json_data,
            params=params,
            timeout=timeout,
        )
    except Exception as e:
        # Convert any exception to a more user-friendly format
        raise WebRequestError(f"HTTP request failed: {str(e)}")


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
