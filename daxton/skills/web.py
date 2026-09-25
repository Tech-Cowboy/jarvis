"""Web skills: search (DuckDuckGo via ddgs), open sites, YouTube, Wikipedia, news."""

from __future__ import annotations

import re
import urllib.parse
import webbrowser
from typing import Annotated

import httpx

from .registry import skill

SITES: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "google calendar": "https://calendar.google.com",
    "google drive": "https://drive.google.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "wikipedia": "https://www.wikipedia.org",
    "amazon": "https://www.amazon.com",
    "netflix": "https://www.netflix.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "weather": "https://weather.com",
    "spotify web": "https://open.spotify.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "stack overflow": "https://stackoverflow.com",
    "hacker news": "https://news.ycombinator.com",
    "odoo": "https://www.odoo.com",
}

_DEFAULT_MAX_RESULTS = 5


def _strip_site_words(name: str) -> str:
    name = name.strip().strip("'\"").rstrip(".!?")
    name = re.sub(r"\s+(website|site|web page|page|homepage)$", "", name, flags=re.I)
    name = re.sub(r"^(the|a)\s+", "", name, flags=re.I)
    return name.strip()


def resolve_site(name: str) -> str:
    """Turn 'youtube', 'github.com' or a full URL into a URL; otherwise a DuckDuckGo search for it."""
    n = _strip_site_words(name)
    low = n.lower()
    if low in SITES:
        return SITES[low]
    if low.startswith(("http://", "https://")):
        return n
    if "." in low and " " not in low:
        return "https://" + n
    return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(n)


def _open(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except Exception:
        return False


@skill()
def open_website(name: Annotated[str, "Site name or address, e.g. 'youtube', 'github.com', 'weather'."]) -> str:
    """Open a website in the default browser by common name or address."""
    url = resolve_site(name)
    ok = _open(url)
    label = _strip_site_words(name)
    return f"Opened {label} in your browser." if ok else f"I couldn't open the browser for {label}."


@skill()
def open_url(url: Annotated[str, "A full URL to open."]) -> str:
    """Open a specific URL in the default browser."""
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    ok = _open(url)
    host = urllib.parse.urlparse(url).netloc or url
    return f"Opened {host}." if ok else f"I couldn't open {host}."


@skill()
def youtube_search(query: Annotated[str, "What to search for on YouTube."]) -> str:
    """Open YouTube search results for a query in the browser."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    ok = _open(url)
    return f"Showing YouTube results for {query}." if ok else "I couldn't open the browser."


@skill()
def web_search(
    query: Annotated[str, "Search query."],
    max_results: Annotated[int, "How many results to fetch (1-10)."] = _DEFAULT_MAX_RESULTS,
) -> str:
    """Search the web and return the top results (title, snippet, source). Summarize them for the user."""
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: the 'ddgs' package is not installed."
    n = max(1, min(int(max_results or _DEFAULT_MAX_RESULTS), 10))
    try:
        results = DDGS().text(query, max_results=n)
    except Exception as e:
        return f"Error: web search failed: {e}"
    if not results:
        return f"No results found for {query}."
    lines = []
    for i, r in enumerate(results, 1):
        host = urllib.parse.urlparse(r.get("href", "")).netloc.replace("www.", "")
        body = " ".join((r.get("body") or "").split())
        lines.append(f"{i}. {r.get('title', '').strip()} ({host}): {body}")
    return f"Search results for '{query}':\n" + "\n".join(lines)


@skill()
def news_search(
    query: Annotated[str, "News topic, e.g. 'horse racing' or 'top news'."],
    max_results: Annotated[int, "How many headlines (1-10)."] = _DEFAULT_MAX_RESULTS,
) -> str:
    """Fetch recent news headlines about a topic."""
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: the 'ddgs' package is not installed."
    n = max(1, min(int(max_results or _DEFAULT_MAX_RESULTS), 10))
    try:
        results = DDGS().news(query, max_results=n)
    except Exception as e:
        return f"Error: news search failed: {e}"
    if not results:
        return f"No news found for {query}."
    lines = []
    for i, r in enumerate(results, 1):
        src = r.get("source") or urllib.parse.urlparse(r.get("url", "")).netloc
        date = (r.get("date") or "")[:10]
        body = " ".join((r.get("body") or "").split())
        lines.append(f"{i}. {r.get('title', '').strip()} ({src}, {date}): {body}")
    return f"News about '{query}':\n" + "\n".join(lines)


# Wikimedia asks API clients to identify themselves: https://meta.wikimedia.org/wiki/User-Agent_policy
WIKI_HEADERS = {"User-Agent": "daxton-ai/0.1 (https://github.com/Tech-Cowboy/daxton-ai; personal use)",
                "Accept": "application/json"}


def _wikipedia_extract(title: str) -> tuple[str, str] | None:
    """(article title, extract) or None if there is no such article."""
    summary_url = "https://en.wikipedia.org/api/rest_v1/page/summary/"
    r = httpx.get(summary_url + urllib.parse.quote(title.replace(" ", "_")), headers=WIKI_HEADERS, timeout=8,
                  follow_redirects=True)
    if r.status_code == 404:
        s = httpx.get("https://en.wikipedia.org/w/api.php",
                      params={"action": "opensearch", "search": title, "limit": 1, "format": "json"},
                      headers=WIKI_HEADERS, timeout=8)
        s.raise_for_status()
        names = s.json()[1]
        if not names:
            return None
        r = httpx.get(summary_url + urllib.parse.quote(names[0].replace(" ", "_")), headers=WIKI_HEADERS, timeout=8,
                      follow_redirects=True)
    r.raise_for_status()
    data = r.json()
    return data.get("title", title), " ".join((data.get("extract") or "").split())


@skill()
def wikipedia_summary(topic: Annotated[str, "Person, place, thing or concept to look up."]) -> str:
    """Get a short encyclopedic summary of a topic from Wikipedia (falls back to a web search)."""
    title = topic.strip().rstrip(".!?")
    try:
        found = _wikipedia_extract(title)
    except Exception as e:
        # Rate limits and outages happen; a web search still answers the question.
        fallback = web_search(title, max_results=3)
        return f"Wikipedia was unavailable ({type(e).__name__}); " + fallback
    if found is None:
        return f"I couldn't find a Wikipedia article about {title}. " + web_search(title, max_results=3)
    name, extract = found
    if not extract:
        return f"Wikipedia has no summary for {name}."
    sentences = re.split(r"(?<=[.!?])\s+", extract)
    return f"{name}: " + " ".join(sentences[:3])
