#!/usr/bin/env python3
"""
Parche PocketStrike-AI — fix bucle infinito de tool calls.
FIX 1: extractor balanceado (reemplaza regex lazy). FIX 2: parser robusto.

Aplicación: generar server_patched.py desde el server.py del Redmi usando
sustituciones de texto exactas sobre las líneas identificadas (5394, 5571,
parse_arguments en 4918-4955). Copia de seguridad: server.py.bak-<fecha>.

Modo de uso (desde scl-01):
  scp este script al Redmi y ejecutar:  python3 patch_ps.py [server.py]
"""
import re, ast, sys, shutil, time

MARKER = "# --- POCKETSTRIKE PARSER FIX v1 ---"

HELPER = '''

# --- POCKETSTRIKE PARSER FIX v1 ---
def extract_tool_call(text):
    """Extrae [TOOL_CALL: name(args)] con balanceo de parentesis; respeta strings y escapes."""
    marker = re.search(r'\\[TOOL_CALL:\\s*(\\w+)\\(', text)
    if not marker:
        return None
    name = marker.group(1)
    start = marker.end()
    depth = 1
    i = start
    in_str = None
    while i < len(text):
        c = text[i]
        if in_str:
            if c == chr(92) and i + 1 < len(text):
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c == '"' or c == "'":
                in_str = c
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    rest = text[i+1:].lstrip()
                    if rest.startswith(']'):
                        return (name, text[start:i], (marker.start(), i + 1 + (len(text[i+1:]) - len(rest))))
                    return None
        i += 1
    return None

def _split_top_level(s):
    parts, buf = [], []
    i = 0
    in_str = None
    depth_paren = 0
    depth_brack = 0
    while i < len(s):
        c = s[i]
        if in_str:
            buf.append(c)
            if c == chr(92) and i + 1 < len(s):
                buf.append(s[i+1])
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c == '"' or c == "'":
                in_str = c
                buf.append(c)
            elif c == '(':
                depth_paren += 1
                buf.append(c)
            elif c == ')':
                depth_paren -= 1
                buf.append(c)
            elif c == '[':
                depth_brack += 1
                buf.append(c)
            elif c == ']':
                depth_brack -= 1
                buf.append(c)
            elif c == ',' and depth_paren == 0 and depth_brack == 0:
                parts.append(''.join(buf))
                buf = []
            else:
                buf.append(c)
        i += 1
    if buf:
        parts.append(''.join(buf))
    return parts

def _unescape_string(raw):
    for q in ('"', "'"):
        try:
            return ast.literal_eval(q + raw + q)
        except Exception:
            pass
    return (raw.replace('\\n', chr(10)).replace('\\t', chr(9))
               .replace('\\"', '"').replace("\\'", "'"))

def parse_arguments_fixed(arg_str):
    arg_str = arg_str.strip()
    if not arg_str:
        return {}
    kwargs = {}
    for part in _split_top_level(arg_str):
        part = part.strip()
        if not part:
            continue
        m = re.match(r'(\\w+)\\s*=\\s*(.*)$', part, re.DOTALL)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if not val:
            kwargs[key] = ''
            continue
        if val[0] == '"' or val[0] == "'":
            raw = val[1:]
            if raw.endswith(val[0]) and not raw.endswith(chr(92) + val[0]):
                raw = raw[:-1]
            kwargs[key] = _unescape_string(raw)
        elif val[0] == '[':
            try:
                kwargs[key] = ast.literal_eval(val)
            except Exception:
                kwargs[key] = val
        else:
            low = val.lower()
            if low == 'true':
                kwargs[key] = True
            elif low == 'false':
                kwargs[key] = False
            elif low == 'none':
                kwargs[key] = None
            else:
                try:
                    kwargs[key] = int(val)
                except ValueError:
                    try:
                        kwargs[key] = float(val)
                    except ValueError:
                        kwargs[key] = val
    return kwargs
# --- FIN POCKETSTRIKE PARSER FIX v1 ---

'''

# Sustituciones sobre el server.py del Redmi
SUBS = [
    # FIX 1 en los dos sitios del loop (5394 y 5571)
    (
        "        match = re.search(r'\\[TOOL_CALL:\\s*(\\w+)\\(([\\s\\S]*?)\\)\\s*\\]', response_text)\n"
        "        if not match:\n"
        "            messages.append({\"role\": \"assistant\", \"content\": response_text})\n"
        "            return response_text, messages\n"
        "            \n"
        "        tool_name = match.group(1)\n"
        "        tool_args_str = match.group(2)\n",
        "        match = extract_tool_call(response_text)\n"
        "        if not match:\n"
        "            messages.append({\"role\": \"assistant\", \"content\": response_text})\n"
        "            return response_text, messages\n"
        "            \n"
        "        tool_name = match[0]\n"
        "        tool_args_str = match[1]\n",
    ),
    (
        "        match = re.search(r'\\[TOOL_CALL:\\s*(\\w+)\\(([\\s\\S]*?)\\)\\s*\\]', accumulated_response)\n"
        "        if not match:\n"
        "            messages.append({\"role\": \"assistant\", \"content\": accumulated_response})\n"
        "            sync_messages = [m for m in messages if m.get(\"role\") != \"system\"]\n"
        "            yield f\"\\n[HISTORY_SYNC]:{json.dumps(sync_messages)}\"\n"
        "            return\n"
        "            \n"
        "        tool_name = match.group(1)\n"
        "        tool_args_str = match.group(2)\n",
        "        match = extract_tool_call(accumulated_response)\n"
        "        if not match:\n"
        "            messages.append({\"role\": \"assistant\", \"content\": accumulated_response})\n"
        "            sync_messages = [m for m in messages if m.get(\"role\") != \"system\"]\n"
        "            yield f\"\\n[HISTORY_SYNC]:{json.dumps(sync_messages)}\"\n"
        "            return\n"
        "            \n"
        "        tool_name = match[0]\n"
        "        tool_args_str = match[1]\n",
    ),
    # FIX 2: interior de parse_arguments delega al parser robusto
    (
        "def parse_arguments(arg_str):\n"
        "    if not arg_str.strip():\n"
        "        return {}\n"
        "    kwargs = {}\n"
        "    pattern = r'(\\w+)\\s*=\\s*(\"[^\"]*\"|\\'[^\\']*\\'|\\[[^\\]]*\\]|[^,]+)'\n",
        "def parse_arguments(arg_str):\n"
        "    if not arg_str.strip():\n"
        "        return {}\n"
        "    return parse_arguments_fixed(arg_str)\n"
        "    kwargs = {}\n"
        "    pattern = r'(\\w+)\\s*=\\s*(\"[^\"]*\"|\\'[^\\']*\\'|\\[[^\\]]*\\]|[^,]+)'\n",
    ),
]


def build_patched(src_path, dst_path):
    src = open(src_path, encoding="utf-8").read()
    if MARKER in src:
        print("Ya parcheado — nada que hacer.")
        return False
    # 1) Insertar helpers justo antes de "def execute_local_tool"
    anchor = "def execute_local_tool(name, args_str):"
    idx = src.find(anchor)
    if idx == -1:
        print("ERROR: anchor execute_local_tool no encontrado")
        return False
    src = src[:idx] + HELPER + src[idx:]
    # 2) Aplicar sustituciones
    for old, new in SUBS:
        if old not in src:
            print("ERROR: patrón no encontrado =>", old[:80])
            return False
        src = src.replace(old, new, 1)
    # 3) Verificación de sintaxis
    compile(src, "server_patched.py", "exec")
    open(dst_path, "w", encoding="utf-8").write(src)
    print("OK: parcheado y compilado ->", dst_path)
    return True


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "server.py"
    dst = src + ".patched"
    if build_patched(src, dst):
        shutil.copy2(src, src + ".bak-" + time.strftime("%Y%m%d-%H%M"))
        print("Backup creado")