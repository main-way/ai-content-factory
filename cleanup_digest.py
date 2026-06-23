#!/usr/bin/env python3
"""Remove LLM analysis/requirements blocks from digest posts (binary-safe)."""
import re

INPUT = "output/digest_2026-06-23_clusterized.md"

with open(INPUT, "rb") as f:
    raw = f.read()

print(f"Loaded: {len(raw)} bytes")

# Decode for processing
content = raw.decode("utf-8", errors="replace")
lines = content.split("\n")
new_lines = []
i = 0
n = len(lines)
cleaned_posts = []

REQ_PATTERNS = [
    # English numbered requirements
    re.compile(r'^\d+\.\s+(?:Need|Write|Use|Explain|Add|Start|Include|Remove|Make|Here|This|New|Your|B2B|No IT|Key факты|Ключевые|Target|Length)'),
    # Russian numbered requirements: "1. Нужно написать", "2. Аудитория", "3. Объём"
    re.compile(r'^\d+\.\s+(?:Нужно|Объём|Аудитория|Без ИТ Использовать|В конце)'),
    # Bullet requirements
    re.compile(r'^-\s+(?:Topic|Requirements|Key angle|Length|Target|No IT|Business|При этом|This article)'),
    # English analysis phrases
    re.compile(r'^(?:Now I need to|Let me write|Let me extract|Let me analyze|Here is a|Below are the key|Analyze this|This article should|The text should)'),
    # Russian analysis phrases
    re.compile(r'^(?:Ключевые факты|Основные моменты|Here is the plan|Анализирую задачу|Материалы:)'),
    # URL-only lines
    re.compile(r'^https?://\S+\s*$'),
]

def is_req(line):
    s = line.strip()
    for p in REQ_PATTERNS:
        if p.match(s):
            return True
    return False

while i < n:
    line = lines[i]
    # Match post header: ## N.  (space after period, then either title or newline)
    m = re.match(r'^## (\d+)\. (?=\S|\n)', line)
    if m:
        post_num = m.group(1)
        new_lines.append(line)
        i += 1
        # Skip blank lines
        while i < n and lines[i].strip() == "":
            new_lines.append(lines[i])
            i += 1
        # Skip requirement lines
        req_count = 0
        while i < n and is_req(lines[i]):
            req_count += 1
            i += 1
        # Skip blank separator
        while i < n and lines[i].strip() == "":
            i += 1
        if req_count:
            cleaned_posts.append(post_num)
        continue
    new_lines.append(line)
    i += 1

result = "\n".join(new_lines)
result_bytes = result.encode("utf-8", errors="replace")

print(f"Before: {len(raw)}, After: {len(result_bytes)}")
print(f"Posts cleaned: {len(cleaned_posts)}: {', '.join(cleaned_posts)}")

with open(INPUT, "wb") as f:
    f.write(result_bytes)

print("Saved!")
