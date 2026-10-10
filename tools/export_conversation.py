import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

def visible_output(value):
    """Keep tool text while replacing inline media with attachment references."""
    if isinstance(value, list):
        return [visible_output(item) for item in value]
    if isinstance(value, dict):
        if value.get("type") in ("image", "input_image", "audio", "input_audio", "video"):
            return {"type": "attachment", "note": "Tool media attachment; binary data omitted. Retain original separately."}
        return {key: visible_output(item) for key, item in value.items()}
    if isinstance(value, str) and value.startswith(("data:image/", "data:audio/", "data:video/")):
        return "[Inline media data omitted; retain original attachment separately.]"
    return value

# Only visible messages and tool interactions. Never export reasoning/system/developer records.
def export(source, directory):
    source=Path(source)
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    content=source.read_bytes()
    records=[]
    malformed=0
    for line in content.decode("utf-8").splitlines():
        try: event=json.loads(line)
        except ValueError:
            malformed+=1
            continue
        if event.get("type")!="response_item": continue
        payload=event.get("payload",{})
        kind=payload.get("type")
        if kind=="message":
            if payload.get("role") not in ("user","assistant"): continue
            if payload.get("channel") in ("analysis","summary"): continue
            # Whitelist text/image references; never copy opaque/internal payload fields.
            parts=[]
            for item in payload.get("content",[]):
                if item.get("type") in ("input_text","output_text","text"):
                    parts.append({"type":"text","text":item.get("text","")})
                elif item.get("type") in ("input_image","image"):
                    parts.append({"type":"attachment","note":"Image attachment; retain original supplied file separately."})
            clean={"type":"message","role":payload["role"],"content":parts}
        elif kind in ("function_call","custom_tool_call"):
            clean={k:payload[k] for k in ("type","name","call_id","arguments","input") if k in payload}
        elif kind in ("function_call_output","custom_tool_call_output"):
            clean={k:visible_output(payload[k]) for k in ("type","call_id","output") if k in payload}
        else: continue
        records.append({"timestamp":event.get("timestamp"),"payload":clean})
    meta={"exported_at":datetime.now(timezone.utc).isoformat(),"source_file":source.name,
          "source_sha256":hashlib.sha256(content).hexdigest(),"source_bytes":len(content),
          "last_included_timestamp":records[-1]["timestamp"] if records else None,
          "visible_record_count":len(records),"malformed_lines":malformed,
          "scope":"Visible user/assistant messages and tool interactions available in this session. Excludes internal reasoning, system/developer instructions and binary image data."}
    (directory/"ai-conversation.jsonl").write_text("\n".join(json.dumps(r,ensure_ascii=False) for r in records)+"\n",encoding="utf-8")
    chunks=["# AI 可见对话与工具记录\n\n",json.dumps(meta,ensure_ascii=False,indent=2),
            "\n\n刷新：python tools/export_conversation.py --source SESSION_JSONL --output artifacts\n\n"]
    for index,record in enumerate(records,1):
        payload=record["payload"]
        chunks.append(f"## {index}. {payload.get('role',payload['type'])} — {record['timestamp']}\n\n")
        if payload["type"]=="message":
            chunks.append("\n\n".join(part.get("text",part.get("note","")) for part in payload["content"]))
        else:
            text=json.dumps(payload,ensure_ascii=False,indent=2)
            fence="`"*(max([len(s) for s in __import__('re').findall(r'`+',text)]+[2])+1)
            chunks.append(f"{fence}json\n{text}\n{fence}")
        chunks.append("\n\n")
    (directory/"ai-conversation.md").write_text("".join(chunks),encoding="utf-8")
    (directory/"conversation-export-meta.json").write_text(json.dumps(meta,indent=2,ensure_ascii=False),encoding="utf-8")
    return meta

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",required=True)
    parser.add_argument("--output",default="artifacts")
    args=parser.parse_args()
    print(json.dumps(export(args.source,args.output),indent=2,ensure_ascii=False))
