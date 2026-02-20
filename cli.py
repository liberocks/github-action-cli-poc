import sys
import time
import json
import zipfile
import io
import requests

# CONFIGURATION
CLIENT_ID = "Ov23lixh0Jo1iw0PIVRw"
REPO_OWNER = "liberocks"
REPO_NAME = "github-action-cli-poc"
WORKFLOW_FILE = "trigger.yml"

def request_device_code():
    print("Requesting device code from GitHub...")
    response = requests.post(
        "https://github.com/login/device/code",
        headers={"Accept": "application/json"},
        data={
            "client_id": CLIENT_ID,
            "scope": "repo"
        }
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        print(f"Error requesting device code: {data.get('error_description')}")
        sys.exit(1)
        
    print(f"\n--- GITHUB AUTHENTICATION ---")
    print(f"Please visit: {data['verification_uri']}")
    print(f"And enter the code: {data['user_code']}")
    print("-----------------------------\n")
    return data

def poll_for_token(device_code, interval):
    print("Waiting for authentication to complete...")
    while True:
        time.sleep(interval)
        response = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
            }
        )
        response.raise_for_status()
        data = response.json()
        
        if "access_token" in data:
            print("Authentication successful!")
            return data["access_token"]
        
        error = data.get("error")
        if error == "authorization_pending":
            # Still waiting on user
            continue
        elif error == "slow_down":
            interval += 5
            continue
        elif error == "expired_token":
            print("The device code has expired. Please run the CLI again.")
            sys.exit(1)
        else:
            print(f"Unexpected error: {data.get('error_description', error)}")
            sys.exit(1)

def get_current_user(token):
    response = requests.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }
    )
    response.raise_for_status()
    return response.json()["login"]

def trigger_workflow(token):
    print("Triggering the GitHub action workflow...")
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        },
        json={
            "ref": "main",
            "inputs": {
                "message": "Triggered securely via OAuth Device Flow CLI"
            }
        }
    )
    response.raise_for_status()
    print("Workflow dispatched successfully.")
    
def get_latest_workflow_run(token, actor):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{WORKFLOW_FILE}/runs"
    # We poll for a few seconds to let GitHub's systems catch up and register the run
    print("Locating the workflow run...")
    for _ in range(10):
        time.sleep(2)
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json"
            },
            params={"actor": actor, "per_page": 1}
        )
        response.raise_for_status()
        data = response.json()
        if data["total_count"] > 0:
            run = data["workflow_runs"][0]
            # Verify the run is recent
            import datetime
            run_time = datetime.datetime.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ")
            run_time = run_time.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            if (now - run_time).total_seconds() < 300: # within 5 minutes
                return run
    print("Failed to locate the workflow run.")
    sys.exit(1)
    
def wait_for_run_completion(token, run_id):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/runs/{run_id}"
    print(f"Waiting for workflow run {run_id} to complete...")
    while True:
        time.sleep(5)
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        response.raise_for_status()
        run = response.json()
        status = run["status"]
        if status == "completed":
            print(f"Workflow run completed with conclusion: {run['conclusion']}")
            return run
        print(f"Current status: {status}...")

def download_and_parse_artifact(token, run_id):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/runs/{run_id}/artifacts"
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }
    )
    response.raise_for_status()
    artifacts = response.json()["artifacts"]
    artifact_url = None
    for artifact in artifacts:
        if artifact["name"] == "workflow-output":
            artifact_url = artifact["archive_download_url"]
            break
            
    if not artifact_url:
        print("Could not find the 'workflow-output' artifact.")
        return
        
    print("Downloading artifact...")
    response = requests.get(
        artifact_url,
        headers={
            "Authorization": f"Bearer {token}",
        }
    )
    response.raise_for_status()
    
    # Extract zip and read JSON
    print("Parsing JSON output...")
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            with z.open("output.json") as f:
                output_data = json.load(f)
                print("\n--- WORKFLOW JSON OUTPUT ---")
                print(json.dumps(output_data, indent=2))
                print("----------------------------\n")
    except Exception as e:
        print(f"Error extracting or parsing artifact: {e}")

def main():
    if CLIENT_ID == "YOUR_OAUTH_APP_CLIENT_ID":
        print("ERROR: Please configure the CLIENT_ID in cli.py before running.")
        sys.exit(1)
        
    # 1. Device Code Request
    device_data = request_device_code()
    
    # 2. Poll for token
    token = poll_for_token(device_data["device_code"], device_data["interval"])
    
    # 3. Get current user's login name so we can filter runs
    actor = get_current_user(token)
    print(f"Authenticated as: {actor}")
    
    # 4. Trigger workflow
    trigger_workflow(token)
    
    # 5. Get the run ID
    run = get_latest_workflow_run(token, actor)
    print(f"Found run ID: {run['id']} at {run['html_url']}")
    
    # 6. Wait for run to finish
    wait_for_run_completion(token, run["id"])
    
    # 7. Download JSON artifact and parse it
    download_and_parse_artifact(token, run["id"])

if __name__ == "__main__":
    main()
