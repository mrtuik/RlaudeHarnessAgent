"""
Runs inside GitHub Actions whenever the app opens a 'candidate' issue.

Flow:
  1. Ask an LLM whether the issue text is really a valid, generalizable
     coding-mistake correction (filters spam / noise / one-off nonsense).
  2. If valid, ask the LLM whether it matches any lesson already waiting
     in pending_candidates.json (same underlying mistake, worded differently).
  3. Increment the matching candidate's count, or create a new candidate.
  4. Once a candidate has been reported by CONFIRM_THRESHOLD separate
     issues, promote it into lessons.json (the file the app fetches for
     every user) and close out all the issues that contributed to it.
"""

import json
import os
import re
import sys
import requests

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPOSITORY = os.environ["GITHUB_REPOSITORY"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ISSUE_NUMBER = int(os.environ["ISSUE_NUMBER"])
ISSUE_TITLE = os.environ.get("ISSUE_TITLE", "") or ""
ISSUE_BODY = os.environ.get("ISSUE_BODY", "") or ""
CONFIRM_THRESHOLD = int(os.environ.get("CONFIRM_THRESHOLD", "3"))

GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
GH_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

LESSONS_PATH = "lessons.json"
PENDING_PATH = "pending_candidates.json"


def groq_chat(system_prompt: str, user_prompt: str) -> str:
    if not GROQ_API_KEY:
        print("No GROQ_API_KEY set, cannot validate. Failing safe (treat as invalid).")
        return ""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": "llama-3.3-70b-versatile",
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def validate_and_extract(title: str, body: str):
    system = (
        "You screen bug reports for an AI coding agent app. Decide if the report "
        "describes a genuine, specific coding/UI mistake the agent made and how "
        "it should behave instead. Reject spam, greetings, vague complaints with "
        "no actionable correction, or anything not about the agent's own output. "
        'Reply with ONLY JSON: {"valid": true or false, "lesson": "one short, '
        'general, actionable instruction the agent should follow next time, '
        'written as a rule, e.g. \'Open image attachment previews full-screen, '
        "never as a small inline thumbnail.'\"}"
    )
    user = f"Title: {title}\n\nBody: {body}"
    result = extract_json(groq_chat(system, user))
    if not result or "valid" not in result:
        return {"valid": False, "lesson": ""}
    return result


def find_match(lesson: str, pending: dict):
    if not pending:
        return None
    options = "\n".join(f"{key}: {value['text']}" for key, value in pending.items())
    system = (
        "You compare a new coding lesson against a list of existing pending "
        "lessons. Reply with ONLY JSON: {\"match_id\": \"<key>\"} if the new "
        "lesson describes the SAME underlying mistake/rule as one in the list, "
        'or {"match_id": null} if it is a genuinely different lesson.'
    )
    user = f"New lesson: {lesson}\n\nExisting pending lessons:\n{options}"
    result = extract_json(groq_chat(system, user))
    if not result:
        return None
    match_id = result.get("match_id")
    return match_id if match_id in pending else None


def gh_comment(issue_number: int, body: str):
    requests.post(
        f"{GITHUB_API}/issues/{issue_number}/comments",
        headers=GH_HEADERS,
        json={"body": body},
        timeout=30,
    )


def gh_close(issue_number: int, extra_labels=None):
    requests.patch(
        f"{GITHUB_API}/issues/{issue_number}",
        headers=GH_HEADERS,
        json={"state": "closed"},
        timeout=30,
    )
    if extra_labels:
        requests.post(
            f"{GITHUB_API}/issues/{issue_number}/labels",
            headers=GH_HEADERS,
            json={"labels": extra_labels},
            timeout=30,
        )


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else default


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main():
    result = validate_and_extract(ISSUE_TITLE, ISSUE_BODY)
    if not result.get("valid"):
        gh_comment(ISSUE_NUMBER, "Not recognized as a specific, actionable lesson. Closing.")
        gh_close(ISSUE_NUMBER, extra_labels=["rejected"])
        return

    lesson_text = (result.get("lesson") or "").strip()
    if not lesson_text:
        gh_comment(ISSUE_NUMBER, "Could not extract a clear lesson from this report. Closing.")
        gh_close(ISSUE_NUMBER, extra_labels=["rejected"])
        return

    pending = load_json(PENDING_PATH, {})
    lessons = load_json(LESSONS_PATH, [])

    match_id = find_match(lesson_text, pending)

    if match_id:
        entry = pending[match_id]
        entry["issues"].append(ISSUE_NUMBER)
        entry["count"] += 1
    else:
        match_id = f"c{len(pending) + 1}_{ISSUE_NUMBER}"
        entry = {"text": lesson_text, "count": 1, "issues": [ISSUE_NUMBER]}
        pending[match_id] = entry

    if entry["count"] >= CONFIRM_THRESHOLD:
        lessons.append(entry["text"])
        save_json(LESSONS_PATH, lessons)
        contributing_issues = entry["issues"]
        del pending[match_id]
        save_json(PENDING_PATH, pending)
        for issue_num in contributing_issues:
            gh_comment(
                issue_num,
                f"Confirmed after {CONFIRM_THRESHOLD} independent reports. "
                "This lesson is now live for every user's agent.",
            )
            gh_close(issue_num, extra_labels=["confirmed"])
    else:
        save_json(PENDING_PATH, pending)
        gh_comment(
            ISSUE_NUMBER,
            f"Recorded ({entry['count']}/{CONFIRM_THRESHOLD} reports so far for this lesson).",
        )
        gh_close(ISSUE_NUMBER, extra_labels=["tracked"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"process_lesson failed: {exc}", file=sys.stderr)
        # Leave the issue open on unexpected failure so nothing is silently lost.
        sys.exit(1)
