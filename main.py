import os
import sys
import json
import zipfile
import io
import subprocess
import requests
import webbrowser
import re

# ── Config ─────────────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO         = os.environ.get("REPO", "")
RUN_ID       = os.environ.get("RUN_ID", "")

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}

MAX_LOG_CHARS = 8000  # keep prompt manageable


# ── GitHub API helpers ──────────────────────────────────────────────────────────

def get_latest_failed_run(repo):
    url = f"https://api.github.com/repos/{repo}/actions/runs"
    params = {"status": "failure", "per_page": 5}
    r = requests.get(url, headers=HEADERS, params=params, verify=False)
    r.raise_for_status()
    runs = r.json().get("workflow_runs", [])
    if not runs:
        print("No failed runs found.")
        sys.exit(0)
    return runs[0]


def get_run_by_id(repo, run_id):
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
    r = requests.get(url, headers=HEADERS, verify=False)
    r.raise_for_status()
    return r.json()


def download_logs(repo, run_id):
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/logs"
    r = requests.get(url, headers=HEADERS, verify=False, allow_redirects=True)
    if r.status_code == 404:
        print("Logs not available (may have expired or run is still in progress).")
        sys.exit(1)
    r.raise_for_status()
    return r.content


def extract_log_text(zip_bytes):
    """Extract and concatenate all log files from the zip."""
    lines = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for name in z.namelist():
                if name.endswith(".txt"):
                    with z.open(name) as f:
                        content = f.read().decode("utf-8", errors="ignore")
                        lines.append(f"=== {name} ===\n{content}")
    except zipfile.BadZipFile:
        print("Could not read log zip file.")
        sys.exit(1)
    return "\n".join(lines)


def extract_failure_lines(log_text):
    """Keep only lines likely related to the failure."""
    keywords = ["error", "failed", "failure", "exception", "traceback", "exit code", "killed", "fatal", "denied", "not found", "timeout"]
    lines = log_text.splitlines()
    relevant = []
    for i, line in enumerate(lines):
        if any(k in line.lower() for k in keywords):
            # include surrounding context (2 lines before, 2 after)
            start = max(0, i - 2)
            end = min(len(lines), i + 3)
            relevant.extend(lines[start:end])
            relevant.append("---")
    # deduplicate while preserving order
    seen = set()
    deduped = []
    for line in relevant:
        if line not in seen:
            seen.add(line)
            deduped.append(line)
    return "\n".join(deduped)


# ── Claude analysis ─────────────────────────────────────────────────────────────

def analyze_with_claude(log_excerpt, run_info):
    prompt = f"""You are a CI/CD pipeline expert. Analyze this GitHub Actions failure log and provide a structured report.

Pipeline: {run_info.get('name', 'Unknown')}
Repo: {run_info.get('repository', {}).get('full_name', REPO)}
Branch: {run_info.get('head_branch', 'Unknown')}
Triggered by: {run_info.get('event', 'Unknown')}
Run URL: {run_info.get('html_url', '')}

FAILURE LOG:
{log_excerpt}

Provide your analysis in this exact format:

## What Failed
[1-2 sentences describing what step or job failed]

## Root Cause
[1-3 sentences explaining why it failed based on the log]

## Suggested Fix
[Concrete, actionable steps to fix the issue]

## Severity
[Critical / High / Medium / Low — and why]
"""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=60, encoding="utf-8"
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("result", result.stdout)
        else:
            return result.stdout or result.stderr
    except FileNotFoundError:
        return "[Claude Code CLI not found — install Claude Code to enable AI analysis]"
    except subprocess.TimeoutExpired:
        return "[Claude analysis timed out]"
    except Exception as e:
        return f"[Analysis error: {e}]"


# ── Report output ───────────────────────────────────────────────────────────────

def print_report(run_info, analysis, log_excerpt):

    # Clean unicode to ASCII — must happen before any print or HTML
    def clean_text(text):
        replacements = {
            "\u2014": " - ",
            "\u2013": " - ",
            "\u2192": "->",
            "\u2190": "<-",
            "\u2019": "'",
            "\u2018": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u00b7": "-",
            "\u2026": "...",
        }
        for k, v in replacements.items():
            text = text.replace(k, v)
        return text

    analysis    = clean_text(analysis)
    log_excerpt = clean_text(log_excerpt)

    print("\n" + "="*60)
    print("  AI PIPELINE LOG ANALYZER")
    print("="*60)
    print(f"  Repo    : {REPO}")
    print(f"  Run     : #{run_info.get('run_number', '?')} - {run_info.get('name', '')}")
    print(f"  Branch  : {run_info.get('head_branch', '')}")
    print(f"  Status  : {run_info.get('conclusion', '').upper()}")
    print(f"  URL     : {run_info.get('html_url', '')}")
    print("="*60)
    print("\n--- AI ANALYSIS ---\n")
    print(analysis)
    print("\n--- RAW LOG EXCERPT ---\n")
    print(log_excerpt[:2000] + ("..." if len(log_excerpt) > 2000 else ""))
    print("\n" + "="*60)

    # Convert markdown analysis to HTML
    def md_to_html(text):
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
        text = re.sub(r'^(\d+)\. (.+)$', r'<li>\2</li>', text, flags=re.MULTILINE)
        text = re.sub(r'```[\w]*\n(.*?)```', lambda m: f'<pre><code>{m.group(1)}</code></pre>', text, flags=re.DOTALL)
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
        text = text.replace("\n\n", "</p><p>").replace("\n", "<br>")
        return f"<p>{text}</p>"

    severity = "failure"
    if "critical" in analysis.lower():
        severity_color = "#c0392b"
        severity_label = "CRITICAL"
    elif "high" in analysis.lower():
        severity_color = "#e67e22"
        severity_label = "HIGH"
    elif "medium" in analysis.lower():
        severity_color = "#f39c12"
        severity_label = "MEDIUM"
    else:
        severity_color = "#27ae60"
        severity_label = "LOW"

    run_url = run_info.get('html_url', '')
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Pipeline Failure Report — Run #{run_info.get('run_number', '?')}</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 24px; }}
  .container {{ max-width: 900px; margin: 0 auto; }}
  .header {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px 24px; margin-bottom: 20px; }}
  .header h1 {{ margin: 0 0 12px 0; font-size: 20px; color: #f0f6fc; }}
  .meta {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px; }}
  .meta-item {{ display: flex; gap: 8px; }}
  .meta-label {{ color: #8b949e; min-width: 70px; }}
  .meta-value {{ color: #c9d1d9; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; background: {severity_color}; color: white; }}
  .section {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px 24px; margin-bottom: 16px; }}
  .section h2 {{ margin: 0 0 12px 0; font-size: 15px; color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 8px; }}
  .section p, .section li {{ font-size: 14px; line-height: 1.6; margin: 6px 0; color: #c9d1d9; }}
  .section ol {{ padding-left: 20px; }}
  code {{ background: #0d1117; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 13px; color: #79c0ff; }}
  pre {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 14px; overflow-x: auto; font-size: 12px; color: #8b949e; line-height: 1.5; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .powered {{ text-align: center; font-size: 12px; color: #484f58; margin-top: 24px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Pipeline Failure Report &mdash; Run #{run_info.get('run_number', '?')}</h1>
    <div class="meta">
      <div class="meta-item"><span class="meta-label">Repo</span><span class="meta-value">{REPO}</span></div>
      <div class="meta-item"><span class="meta-label">Status</span><span class="badge">{severity_label}</span></div>
      <div class="meta-item"><span class="meta-label">Branch</span><span class="meta-value">{run_info.get('head_branch', '')}</span></div>
      <div class="meta-item"><span class="meta-label">Workflow</span><span class="meta-value">{run_info.get('name', '')}</span></div>
      <div class="meta-item"><span class="meta-label">URL</span><span class="meta-value"><a href="{run_url}" target="_blank">View on GitHub</a></span></div>
    </div>
  </div>

  <div class="section">
    {md_to_html(analysis)}
  </div>

  <div class="section">
    <h2>Raw Log Excerpt</h2>
    <pre>{log_excerpt[:3000].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}{"..." if len(log_excerpt) > 3000 else ""}</pre>
  </div>

  <div class="powered">Analyzed by AI Pipeline Log Analyzer &mdash; github.com/montanawyethm03-max/pipeline-log-analyzer</div>
</div>
</body>
</html>"""

    report_path = os.path.abspath(f"pipeline_report_run{run_info.get('run_number', 'unknown')}.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Report saved: {report_path}")
    webbrowser.open(f"file:///{report_path}")


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not set.")
        sys.exit(1)
    if not REPO:
        print("ERROR: REPO not set (e.g. montanawyethm03-max/fuel-forecast-bot-py)")
        sys.exit(1)

    print(f"Fetching pipeline data for: {REPO}")

    if RUN_ID:
        run_info = get_run_by_id(REPO, RUN_ID)
    else:
        print("Looking for latest failed run...")
        run_info = get_latest_failed_run(REPO)

    print(f"Found: Run #{run_info.get('run_number')} — {run_info.get('name')} ({run_info.get('conclusion')})")
    print("Downloading logs...")

    zip_bytes = download_logs(REPO, run_info["id"])
    full_log  = extract_log_text(zip_bytes)
    excerpt   = extract_failure_lines(full_log)

    if not excerpt.strip():
        excerpt = full_log[:MAX_LOG_CHARS]

    excerpt = excerpt[:MAX_LOG_CHARS]

    print("Analyzing with Claude...")
    analysis = analyze_with_claude(excerpt, run_info)

    print_report(run_info, analysis, excerpt)


if __name__ == "__main__":
    main()
