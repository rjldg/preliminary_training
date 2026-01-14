
#!/usr/bin/env python3
"""
Azure Speech — Batch Transcription sample

Features:
- Submit a batch transcription job that points to an input Blob container (SAS URL)
- Poll job status with conservative cadence and exponential backoff
- On success, list and optionally download result files (.json, etc.) from the output container
- Key-based auth (or swap to Entra ID token if you already have one)

IMPORTANT:
- You must pre-create your input container with audio files and generate a SAS URL.
- You must pre-create an output container and generate a SAS URL where the service will write results.

References:
- Batch transcription overview & workflow
  (Azure Speech) — Microsoft Learn

Author: Your Name
"""

import os
import sys
import time
import json
import urllib.parse
import requests
from typing import Dict, Any, Optional

# ---------------------------
# Configuration (env vars)
# ---------------------------

SPEECH_REGION = os.getenv("SPEECH_REGION", "<your-region>")  # e.g., "eastus"
SPEECH_KEY = os.getenv("SPEECH_KEY", "<your-speech-key>")    # or use ENTRA_ACCESS_TOKEN instead
ENTRA_ACCESS_TOKEN = os.getenv("ENTRA_ACCESS_TOKEN", "")     # optional: bearer token

# API version for batch transcription
API_VERSION = os.getenv("API_VERSION", "2024-11-15")

# Job metadata
DISPLAY_NAME = os.getenv("DISPLAY_NAME", "MyBatchJob")
DESCRIPTION = os.getenv("DESCRIPTION", "Transcribe multiple audio files from a container")

# Locale: if known, set one (e.g., "en-US"). If you intend language auto-ID, check docs for model support.
LOCALE = os.getenv("LOCALE", "en-US")

# Input container SAS URL (READ access)
# Example: "https://<account>.blob.core.windows.net/<container>?<sas>"
INPUT_CONTAINER_SAS_URL = os.getenv("INPUT_CONTAINER_SAS_URL", "")

# Output container SAS URL (WRITE access)
OUTPUT_CONTAINER_SAS_URL = os.getenv("OUTPUT_CONTAINER_SAS_URL", "")

# Optional: phrase list to boost domain terms (if supported by your API version/model)
PHRASES = json.loads(os.getenv("PHRASES_JSON", "[]"))  # e.g., '["Contoso", "Rehaan"]'

# Polling controls
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "60"))  # 1 minute default
MAX_POLL_MINUTES = int(os.getenv("MAX_POLL_MINUTES", "180"))            # up to 3 hours
BACKOFF_MULTIPLIER = float(os.getenv("BACKOFF_MULTIPLIER", "1.5"))       # exponential backoff

# Download results locally
DOWNLOAD_RESULTS = os.getenv("DOWNLOAD_RESULTS", "false").lower() == "true"
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "batch_results")

# ---------------------------
# Helpers
# ---------------------------

def endpoint_base(region: str) -> str:
    # Batch transcription “create” (collection) endpoint
    return f"https://{region}.api.cognitive.microsoft.com/speechtotext/batchtranscriptions"

def headers() -> Dict[str, str]:
    if ENTRA_ACCESS_TOKEN:
        return {"Authorization": f"Bearer {ENTRA_ACCESS_TOKEN}"}
    elif SPEECH_KEY and SPEECH_KEY != "<your-speech-key>":
        return {"Ocp-Apim-Subscription-Key": SPEECH_KEY}
    else:
        raise ValueError("Configure authentication: set ENTRA_ACCESS_TOKEN or SPEECH_KEY.")

def create_body() -> Dict[str, Any]:
    """
    Minimal request body to point the service at your input container and to write to your output container.
    Depending on the latest REST schema, 'recordingsUrl' (input) and 'resultsContainerUrl' (output) are typical.
    """
    body: Dict[str, Any] = {
        "displayName": DISPLAY_NAME,
        "description": DESCRIPTION,
        "locale": LOCALE,
        "recordingsUrl": INPUT_CONTAINER_SAS_URL,      # Input container with audio files (SAS)
        "resultsContainerUrl": OUTPUT_CONTAINER_SAS_URL # Output container for transcription (SAS)
    }

    # Optional: boost keywords via phrase list if supported by API/model
    if PHRASES:
        body["properties"] = {
            "wordLevelTimestampsEnabled": True,  # handy for many workloads
            "diagnosticMode": False,
            "profanityFilterMode": "Masked",     # None|Masked|Removed|Tags
            "punctuationMode": "DictatedAndAutomatic",
            "speechRecognitionPhrases": PHRASES
        }

    return body

def submit_job() -> str:
    url = f"{endpoint_base(SPEECH_REGION)}?api-version={API_VERSION}"
    resp = requests.post(url, headers=headers(), json=create_body(), timeout=60)
    if not resp.ok:
        print("Create failed:", resp.status_code, resp.text)
        resp.raise_for_status()

    job = resp.json()
    job_id = job.get("id") or job.get("self") or ""
    if not job_id:
        # Some APIs return a 'self' link with the job id embedded
        # Try to extract from location header or 'links' if present.
        loc = resp.headers.get("Location")
        if loc:
            # Often ends with .../batchtranscriptions/{id}?api-version=...
            parsed = urllib.parse.urlparse(loc)
            path_parts = parsed.path.rstrip("/").split("/")
            if path_parts:
                job_id = path_parts[-1]

    if not job_id:
        raise RuntimeError(f"Could not determine job id. Response: {job}")

    print(f"Submitted job: {job_id}")
    return job_id

def get_job(job_id: str) -> Dict[str, Any]:
    url = f"{endpoint_base(SPEECH_REGION)}/{job_id}?api-version={API_VERSION}"
    resp = requests.get(url, headers=headers(), timeout=60)
    if not resp.ok:
        print("Get job failed:", resp.status_code, resp.text)
        resp.raise_for_status()
    return resp.json()

def list_files(job_id: str) -> Dict[str, Any]:
    """
    Some API versions expose file listing under a job:
    GET /batchtranscriptions/{id}/files
    If your version differs, consult the REST reference and adjust the path.
    """
    url = f"{endpoint_base(SPEECH_REGION)}/{job_id}/files?api-version={API_VERSION}"
    resp = requests.get(url, headers=headers(), timeout=60)
    if not resp.ok:
        print("List files failed:", resp.status_code, resp.text)
        resp.raise_for_status()
    return resp.json()

def download_file(file_url: str, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    fname = os.path.basename(urllib.parse.urlparse(file_url).path)
    local_path = os.path.join(out_dir, fname or "result.json")
    with requests.get(file_url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    print(f"Downloaded -> {local_path}")

def monitor_until_done(job_id: str) -> Dict[str, Any]:
    """
    Poll job status with exponential backoff up to MAX_POLL_MINUTES.
    Recommended cadence: not more often than once per minute (docs advise ~10 minutes).
    """
    status = None
    waited = 0.0
    interval = POLL_INTERVAL_SECONDS

    print(f"Polling job {job_id} every ~{int(interval)}s (up to {MAX_POLL_MINUTES} minutes)...")

    while waited < MAX_POLL_MINUTES * 60:
        job = get_job(job_id)
        status = job.get("status") or job.get("state")  # Typical enums: NotStarted | Running | Succeeded | Failed

        print(f"[+{int(waited)}s] Status: {status}")

        if status in ("Succeeded", "Failed", "Cancelled"):
            return job

        time.sleep(interval)
        waited += interval
        interval = min(interval * BACKOFF_MULTIPLIER, 10 * 60)  # don’t exceed 10 minutes between polls

    raise TimeoutError(f"Job {job_id} did not complete within {MAX_POLL_MINUTES} minutes.")

def main():
    # Basic validation
    missing = []
    if not SPEECH_REGION or SPEECH_REGION.startswith("<"):
        missing.append("SPEECH_REGION")
    if not (ENTRA_ACCESS_TOKEN or (SPEECH_KEY and not SPEECH_KEY.startswith("<"))):
        missing.append("SPEECH_KEY or ENTRA_ACCESS_TOKEN")
    if not INPUT_CONTAINER_SAS_URL:
        missing.append("INPUT_CONTAINER_SAS_URL")
    if not OUTPUT_CONTAINER_SAS_URL:
        missing.append("OUTPUT_CONTAINER_SAS_URL")

    if missing:
        print("Missing configuration:", ", ".join(missing))
        print("Set env vars before running. See the header comments for details.")
        sys.exit(1)

    # Submit
    job_id = submit_job()

    # Monitor
    job = monitor_until_done(job_id)

    status = job.get("status") or job.get("state")
    print("\n=== Final Job Status ===")
    print(json.dumps(job, indent=2))

    if status != "Succeeded":
        print("\nJob did not succeed. Exiting.")
        sys.exit(2)

    # List produced files (URIs generally contain SAS for direct download)
    print("\nListing job files...")
    files_payload = list_files(job_id)
    files = files_payload.get("values") or files_payload.get("files") or []

    if not files:
        print("No files listed by the API. Check your output container directly.")
        return

    # Show and optionally download results
    print("\n=== Files ===")
    for f in files:
        name = f.get("name", "")
        kind = f.get("kind", "")
        url = f.get("links", {}).get("contentUrl") or f.get("contentUrl") or f.get("url")
        print(f"- {name} [{kind}] -> {url}")

        # Download only 'transcription' or 'result' kinds unless you want logs too
        if DOWNLOAD_RESULTS and url and (("transcription" in (kind or "").lower()) or ("result" in (kind or "").lower())):
            download_file(url, DOWNLOAD_DIR)

    print("\nDone.")

if __name__ == "__main__":
    main()
