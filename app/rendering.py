"""Bounded, sanitized Markdown for conversation prose, never tool output."""

from markdown_it import MarkdownIt
import nh3


MAX_TEXT = 65536;
MAX_HTML = 262144;
markdown = MarkdownIt( "commonmark", { "html": False, "linkify": False } ).enable( "table" );
TAGS = { "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "code", "em", "strong", "s", "a", "table", "thead", "tbody", "tr", "th", "td" };


def render_markdown( text: str ) -> str | None:
    if len( text.encode( "utf-8" ) ) > MAX_TEXT:
        return None;
    try:
        html = nh3.clean( markdown.render( text ), tags=TAGS, attributes={ "a": { "href", "title" }, "ol": { "start" } }, url_schemes={ "https", "http", "mailto" }, link_rel="noopener noreferrer", strip_comments=True );
        return html if len( html.encode( "utf-8" ) ) <= MAX_HTML else None;
    except Exception:
        return None;
