"""Convert Warp conversations into chronological, bounded actor turns."""

from typing import Any


def pretty_tool( value: Any, depth: int = 0 ) -> str:
    if isinstance( value, dict ):
        if depth >= 8:
            return "…";
        return "\n".join( "  " * depth + str( key ).replace( "_", " " ) + ": " + ( pretty_tool( item, depth + 1 ) if not isinstance( item, ( dict, list ) ) else "\n" + pretty_tool( item, depth + 1 ) ) for key, item in value.items() );
    if isinstance( value, list ):
        if depth >= 8:
            return "…";
        return "\n".join( "  " * depth + "• " + ( pretty_tool( item, depth + 1 ) if not isinstance( item, ( dict, list ) ) else "\n" + pretty_tool( item, depth + 1 ) ) for item in value );
    return str( value ) if value is not None else "";


def pieces( text: str, limit: int = 3072 ) -> list[str]:
    encoded = text.encode( "utf-8" );
    if not encoded:
        return [];
    chunks: list[str] = [];
    start = 0;
    while start < len( encoded ):
        end = min( start + limit, len( encoded ) );
        while end < len( encoded ) and encoded[end] & 0xC0 == 0x80:
            end -= 1;
        chunks.append( encoded[start:end].decode( "utf-8" ) );
        start = end;
    return chunks;


def conversation_turns( conversation: dict[str, Any] ) -> list[dict[str, Any]]:
    messages: list[tuple[str, int, dict]] = [];

    def visit( step: dict ) -> None:
        for message in step.get( "messages", [] ):
            if isinstance( message, dict ):
                messages.append( ( str( message.get( "timestamp" ) or "" ), len( messages ), message ) );
        for child in step.get( "steps", [] ):
            if isinstance( child, dict ):
                visit( child );

    for step in conversation.get( "steps", [] ):
        if isinstance( step, dict ):
            visit( step );
    messages.sort( key=lambda item: ( item[0], item[1] ) );
    turns: list[dict[str, Any]] = [];
    for timestamp, message_index, message in messages:
        role = { "user": "User", "assistant": "Agent", "tool": "Tool", "system": "System" }.get( str( message.get( "role" ) ), "Agent" );
        for block_index, block in enumerate( message.get( "content", [] ) ):
            if not isinstance( block, dict ):
                continue;
            kind = block.get( "type" );
            if kind == "text" and isinstance( block.get( "text" ), str ):
                actor, title, body, tool = role, "", block["text"], False;
            elif kind in ( "action", "action_result" ):
                value = block.get( "input" ) if kind == "action" else block.get( "output" );
                actor, title, body, tool = "Tool", f"{block.get( 'name' ) or 'unknown'} · {'call' if kind == 'action' else 'result'}", pretty_tool( value ), True;
            else:
                continue;
            chunks = pieces( body if body or not tool else "Command/input unavailable." if kind == "action" else "Output unavailable." );
            block_id = f"{message.get( 'id' ) or message_index}:{block_index}";
            for index, chunk in enumerate( chunks ):
                turns.append( { "actor": actor, "title": title, "timestamp": timestamp, "body": chunk, "tool": tool, "continued": index > 0, "block_id": block_id, "fragment": index, "fragments": len( chunks ), "kind": kind, "action_id": block.get( "action_id" ) or block.get( "tool_call_id" ) or block.get( "id" ) } );
    return turns;


def turn_window( turns: list[dict], size: int, before: int | None = None, after: int | None = None ) -> dict:
    total = len( turns );
    if before is not None and after is not None:
        raise ValueError( "Choose either before or after" );
    cursor = before if before is not None else after;
    if cursor is not None and not 0 <= cursor <= total:
        raise ValueError( "History cursor is no longer valid" );
    if after is not None:
        start, end, used = after, after, 0;
        while end < total:
            cost = len( str( turns[end]["body"] ).encode( "utf-8" ) ) + 128;
            if end > start and used + cost > size:
                break;
            used += cost;
            end += 1;
    else:
        start, end, used = before if before is not None else total, before if before is not None else total, 0;
        while start > 0:
            cost = len( str( turns[start - 1]["body"] ).encode( "utf-8" ) ) + 128;
            if start < end and used + cost > size:
                break;
            used += cost;
            start -= 1;
    return { "turns": turns[start:end], "start": start, "end": end, "total": total, "older": start > 0, "newer": end < total };
