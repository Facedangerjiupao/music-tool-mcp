import os
import time
import json
import logging
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# Import the core MCP application and logic
from server import (
    app as mcp_app,
    get_sonic_signature,
    get_job_status,
    list_jobs,
    check_health,
    prepare_media,
    JOB_STORE,
    JobStatus,
    JOBS_ROOT
)

# Logger setup
logger = logging.getLogger("audio-sonic-cloud")

# 1. Initialize FastAPI
app = FastAPI(
    title="Audio Sonic Signature Cloud API",
    description="REST API and MCP SSE Server wrapper for Sonic Signature analysis.",
    version="1.0.0"
)

# 2. Add CORS Middleware to support web frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Mount the FastMCP ASGI app.
# FastMCP exposes its ASGI application via mcp_app.app
# Mounting it at /mcp makes the MCP SSE endpoint reachable at:
# GET http://<host>:<port>/mcp/sse
# POST http://<host>:<port>/mcp/messages
# MCP 1.27 adds ASGI root_path to the message endpoint itself.
# Passing mount_path here too would advertise /mcp/mcp/messages/.
app.mount("/mcp", mcp_app.sse_app())

# ── Pydantic Request Models ──────────────────────────────────────────────────
class AnalysisRequest(BaseModel):
    url: str
    job_id: str | None = None


class MediaRequest(BaseModel):
    source: str
    mode: str = "rhythm"
    media_type: str = "audio"
    beat_engine: str = "librosa"
    fps: float = 30.0
    keep_media: bool = True
    separate: bool = False
    job_id: str | None = None


@app.post("/api/v1/media", tags=["Workflow"])
async def trigger_media(request: MediaRequest):
    """Queue URL/local media extraction and optional raw feature timestamps."""
    result = json.loads(await prepare_media(**request.model_dump()))
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["error"])
    return result

# ── REST API Endpoints ────────────────────────────────────────────────────────

@app.post("/api/v1/analyze", tags=["Analysis"])
async def trigger_analysis(request: AnalysisRequest):
    """
    Submits a YouTube URL for sonic analysis. Runs asynchronously in the background.
    """
    try:
        # Call the underlying tool function from server.py
        result_json_str = await get_sonic_signature(url=request.url, job_id=request.job_id)
        result = json.loads(result_json_str)
        
        if result.get("status", result.get("header", {}).get("status")) == JobStatus.ERROR:
            raise HTTPException(status_code=400, detail=result.get("error", {}).get("message", "Validation failed"))
            
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error submitting analysis job: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/jobs/{job_id}", tags=["Jobs"])
async def get_job(job_id: str):
    """
    Returns the status and payload of a specific job.
    """
    status_str = await get_job_status(job_id)
    status_data = json.loads(status_str)
    
    if isinstance(status_data.get("error"), str) and "not found" in status_data["error"].lower():
        raise HTTPException(status_code=404, detail=status_data["error"])
        
    return status_data

@app.get("/api/v1/jobs", tags=["Jobs"])
async def get_all_jobs():
    """
    Lists all in-progress and completed jobs.
    """
    jobs_str = await list_jobs()
    return json.loads(jobs_str)

@app.get("/api/v1/health", tags=["System"])
async def get_health():
    """
    Performs system health check verifying environment dependencies (FFmpeg, libraries).
    """
    health_str = await check_health()
    return json.loads(health_str)

# ── Premium Glassmorphic Job Dashboard ───────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get_dashboard():
    """
    Serves a stunning, premium dark-mode dashboard for monitoring jobs.
    """
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Audio Sonic - Cloud Control Hub</title>
        <!-- Modern Typography -->
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-base: #08070b;
                --bg-glass: rgba(18, 16, 26, 0.65);
                --bg-card: rgba(25, 22, 38, 0.45);
                --border-glass: rgba(255, 255, 255, 0.08);
                --border-active: rgba(147, 51, 234, 0.35);
                
                --primary: #a855f7;
                --primary-glow: rgba(168, 85, 247, 0.2);
                --secondary: #ec4899;
                --success: #10b981;
                --warning: #f59e0b;
                --error: #ef4444;
                --text-main: #f3f4f6;
                --text-muted: #9ca3af;
            }

            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }

            body {
                font-family: 'Outfit', sans-serif;
                background-color: var(--bg-base);
                background-image: 
                    radial-gradient(at 0% 0%, rgba(124, 58, 237, 0.12) 0px, transparent 50%),
                    radial-gradient(at 100% 100%, rgba(236, 72, 153, 0.08) 0px, transparent 50%);
                color: var(--text-main);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                overflow-x: hidden;
            }

            header {
                padding: 2rem 4rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid var(--border-glass);
                backdrop-filter: blur(12px);
                background: rgba(8, 7, 11, 0.4);
                position: sticky;
                top: 0;
                z-index: 100;
            }

            .logo-container {
                display: flex;
                align-items: center;
                gap: 0.75rem;
            }

            .logo-icon {
                width: 2.2rem;
                height: 2.2rem;
                background: linear-gradient(135deg, var(--primary), var(--secondary));
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                box-shadow: 0 0 15px var(--primary-glow);
                font-weight: 700;
                font-size: 1.1rem;
                color: white;
            }

            h1 {
                font-size: 1.5rem;
                font-weight: 700;
                background: linear-gradient(to right, #ffffff, #d8b4fe);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                letter-spacing: -0.5px;
            }

            .connection-badge {
                display: flex;
                align-items: center;
                gap: 0.5rem;
                font-size: 0.85rem;
                font-weight: 600;
                padding: 0.5rem 1rem;
                background: var(--bg-card);
                border: 1px solid var(--border-glass);
                border-radius: 20px;
                color: var(--text-muted);
            }

            .connection-badge::before {
                content: '';
                display: inline-block;
                width: 8px;
                height: 8px;
                background-color: var(--success);
                border-radius: 50%;
                box-shadow: 0 0 8px var(--success);
            }

            main {
                flex: 1;
                max-width: 1300px;
                width: 100%;
                margin: 2.5rem auto;
                padding: 0 2rem;
                display: grid;
                grid-template-columns: 1.1fr 0.9fr;
                gap: 2.5rem;
            }

            @media (max-width: 1024px) {
                main {
                    grid-template-columns: 1fr;
                }
            }

            .glass-panel {
                background: var(--bg-glass);
                border: 1px solid var(--border-glass);
                border-radius: 20px;
                padding: 2rem;
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
                transition: border-color 0.3s ease;
            }

            .glass-panel:hover {
                border-color: var(--border-active);
            }

            h2 {
                font-size: 1.25rem;
                margin-bottom: 1.5rem;
                color: #fff;
                font-weight: 600;
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }

            .form-group {
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
                margin-bottom: 1.5rem;
            }

            label {
                font-size: 0.9rem;
                color: var(--text-muted);
                font-weight: 500;
            }

            .input-wrapper {
                position: relative;
                display: flex;
                gap: 0.75rem;
            }

            input[type="text"] {
                flex: 1;
                background: rgba(0, 0, 0, 0.45);
                border: 1px solid var(--border-glass);
                border-radius: 12px;
                padding: 0.85rem 1.2rem;
                color: #fff;
                font-family: inherit;
                font-size: 0.95rem;
                outline: none;
                transition: all 0.3s ease;
            }

            input[type="text"]:focus {
                border-color: var(--primary);
                box-shadow: 0 0 10px rgba(168, 85, 247, 0.15);
            }

            button {
                background: linear-gradient(135deg, var(--primary), #8b5cf6);
                color: white;
                border: none;
                border-radius: 12px;
                padding: 0.85rem 1.5rem;
                font-family: inherit;
                font-size: 0.95rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s ease;
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }

            button:hover {
                transform: translateY(-2px);
                box-shadow: 0 5px 15px var(--primary-glow);
            }

            button:active {
                transform: translateY(0);
            }

            /* Job Feed / List styling */
            .jobs-container {
                display: flex;
                flex-direction: column;
                gap: 1rem;
                max-height: 480px;
                overflow-y: auto;
                padding-right: 0.5rem;
            }

            .jobs-container::-webkit-scrollbar {
                width: 6px;
            }
            .jobs-container::-webkit-scrollbar-track {
                background: transparent;
            }
            .jobs-container::-webkit-scrollbar-thumb {
                background: rgba(255, 255, 255, 0.1);
                border-radius: 10px;
            }

            .job-card {
                background: var(--bg-card);
                border: 1px solid var(--border-glass);
                border-radius: 12px;
                padding: 1.2rem;
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
                cursor: pointer;
                transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            }

            .job-card:hover {
                background: rgba(25, 22, 38, 0.7);
                border-color: rgba(255, 255, 255, 0.15);
                transform: translateX(4px);
            }

            .job-card.active {
                border-color: var(--primary);
                box-shadow: 0 0 8px var(--primary-glow);
            }

            .job-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
            }

            .job-id {
                font-family: 'JetBrains Mono', monospace;
                font-size: 0.85rem;
                font-weight: 500;
                color: var(--text-main);
            }

            .status-badge {
                font-size: 0.75rem;
                font-weight: 600;
                text-transform: uppercase;
                padding: 0.25rem 0.65rem;
                border-radius: 12px;
                letter-spacing: 0.5px;
            }

            .status-queued { background: rgba(245, 158, 11, 0.15); color: var(--warning); border: 1px solid rgba(245, 158, 11, 0.3); }
            .status-running { background: rgba(59, 130, 246, 0.15); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.3); }
            .status-success { background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); }
            .status-error { background: rgba(239, 68, 68, 0.15); color: var(--error); border: 1px solid rgba(239, 68, 68, 0.3); }

            .job-details {
                font-size: 0.85rem;
                color: var(--text-muted);
                display: grid;
                grid-template-columns: auto 1fr;
                gap: 0.25rem 1rem;
            }

            .job-time {
                align-self: end;
                font-size: 0.75rem;
                color: var(--text-muted);
            }

            /* Preview panel */
            .preview-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 1.5rem;
                border-bottom: 1px solid var(--border-glass);
                padding-bottom: 1rem;
            }

            .preview-title-block {
                display: flex;
                flex-direction: column;
                gap: 0.25rem;
            }

            .preview-subtitle {
                font-size: 0.8rem;
                color: var(--text-muted);
            }

            .payload-box {
                background: rgba(0, 0, 0, 0.5);
                border: 1px solid var(--border-glass);
                border-radius: 12px;
                padding: 1.5rem;
                max-height: 520px;
                overflow: auto;
                font-family: 'JetBrains Mono', monospace;
                font-size: 0.85rem;
                line-height: 1.5;
                color: #d1d5db;
            }

            .payload-box::-webkit-scrollbar {
                width: 6px;
                height: 6px;
            }
            .payload-box::-webkit-scrollbar-thumb {
                background: rgba(255, 255, 255, 0.1);
                border-radius: 10px;
            }

            .empty-state {
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 4rem 2rem;
                color: var(--text-muted);
                text-align: center;
                gap: 1rem;
            }

            .empty-icon {
                font-size: 3rem;
                opacity: 0.35;
            }

            /* Endpoint Reference list */
            .endpoint-list {
                margin-top: 1.5rem;
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
            }

            .endpoint-item {
                background: rgba(0, 0, 0, 0.2);
                border-radius: 10px;
                padding: 0.75rem 1rem;
                display: flex;
                align-items: center;
                justify-content: space-between;
                font-size: 0.8rem;
                border: 1px solid rgba(255, 255, 255, 0.03);
            }

            .method-badge {
                font-weight: 700;
                padding: 0.2rem 0.5rem;
                border-radius: 4px;
                font-size: 0.7rem;
            }
            .method-post { background: rgba(16, 185, 129, 0.1); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.2); }
            .method-get { background: rgba(59, 130, 246, 0.1); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.2); }

            .endpoint-path {
                font-family: 'JetBrains Mono', monospace;
                color: #e9d5ff;
            }
        </style>
    </head>
    <body>
        <header>
            <div class="logo-container">
                <div class="logo-icon">♬</div>
                <div>
                    <h1>Audio Sonic</h1>
                    <p style="font-size:0.75rem; color:var(--text-muted);">Cloud Control Hub</p>
                </div>
            </div>
            <div class="connection-badge">Active Engine</div>
        </header>

        <main>
            <!-- Left Panel: Job Submission & List -->
            <div style="display: flex; flex-direction: column; gap: 2.5rem;">
                <!-- Section 1: Submit URL -->
                <div class="glass-panel">
                    <h2>Submit Audio Signature Job</h2>
                    <div class="form-group">
                        <label for="youtube-url">YouTube Video/Track URL</label>
                        <div class="input-wrapper">
                            <input type="text" id="youtube-url" placeholder="https://www.youtube.com/watch?v=..." />
                            <button onclick="submitJob()">
                                <span>Analyze</span>
                                <span style="font-size:1.1rem">⚡</span>
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Section 2: Active Feed -->
                <div class="glass-panel" style="flex: 1;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
                        <h2>Recent Analysis Jobs</h2>
                        <button onclick="fetchJobs()" style="padding: 0.4rem 0.75rem; font-size: 0.8rem; background: var(--bg-card); border: 1px solid var(--border-glass);">
                            Refresh ⟳
                        </button>
                    </div>
                    
                    <div class="jobs-container" id="jobs-list">
                        <!-- Jobs loaded dynamically -->
                        <div class="empty-state">
                            <div class="empty-icon">📂</div>
                            <p>No jobs processed in this session.</p>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Right Panel: Job View / Payload details -->
            <div style="display: flex; flex-direction: column; gap: 2.5rem;">
                <div class="glass-panel" style="height: 100%; display: flex; flex-direction: column;">
                    <div class="preview-header">
                        <div class="preview-title-block">
                            <h2 style="margin:0;">Signature Payload</h2>
                            <span class="preview-subtitle" id="selected-job-id">Select a job from the feed to view full data</span>
                        </div>
                        <div id="selected-job-status"></div>
                    </div>

                    <div style="flex:1; display:flex; flex-direction:column; justify-content:center;">
                        <pre class="payload-box" id="json-viewer">Select a job from the left to view the finalized Sonic Signature payload...</pre>
                    </div>
                </div>

                <!-- API Reference Summary -->
                <div class="glass-panel">
                    <h2>Cloud API Reference</h2>
                    <div class="endpoint-list">
                        <div class="endpoint-item">
                            <span class="method-badge method-get">GET</span>
                            <span class="endpoint-path">/mcp/sse</span>
                            <span style="color:var(--text-muted);">MCP Agent SSE Tunnel</span>
                        </div>
                        <div class="endpoint-item">
                            <span class="method-badge method-post">POST</span>
                            <span class="endpoint-path">/api/v1/analyze</span>
                            <span style="color:var(--text-muted);">Trigger Analysis Job</span>
                        </div>
                        <div class="endpoint-item">
                            <span class="method-badge method-get">GET</span>
                            <span class="endpoint-path">/api/v1/jobs/{id}</span>
                            <span style="color:var(--text-muted);">Get Job Status</span>
                        </div>
                        <div class="endpoint-item">
                            <span class="method-badge method-get">GET</span>
                            <span class="endpoint-path">/api/v1/health</span>
                            <span style="color:var(--text-muted);">Environment Health</span>
                        </div>
                    </div>
                </div>
            </div>
        </main>

        <script>
            let activeJobId = null;
            
            // Format timestamps beautifully
            function formatTime(timestamp) {
                if (!timestamp) return '';
                const date = new Date(timestamp * 1000);
                return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            }

            async function submitJob() {
                const urlInput = document.getElementById("youtube-url");
                const url = urlInput.value.trim();
                
                if (!url) {
                    alert("Please enter a valid YouTube URL.");
                    return;
                }

                try {
                    const response = await fetch("/api/v1/analyze", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ url: url })
                    });
                    
                    const data = await response.json();
                    if (!response.ok) {
                        throw new Error(data.detail || "Failed to submit job");
                    }
                    
                    urlInput.value = "";
                    fetchJobs();
                    // Auto select the newly submitted job
                    if (data.job_id) {
                        selectJob(data.job_id);
                    }
                } catch (err) {
                    alert("Error: " + err.message);
                }
            }

            async function fetchJobs() {
                try {
                    const response = await fetch("/api/v1/jobs");
                    const jobs = await response.json();
                    
                    const container = document.getElementById("jobs-list");
                    if (!jobs || jobs.length === 0) {
                        container.innerHTML = `
                            <div class="empty-state">
                                <div class="empty-icon">📂</div>
                                <p>No active or completed jobs.</p>
                            </div>
                        `;
                        return;
                    }
                    
                    container.innerHTML = "";
                    
                    // Sort descending by started_at (if available)
                    jobs.sort((a, b) => (b.started_at || 0) - (a.started_at || 0));

                    jobs.forEach(job => {
                        const isSelected = job.job_id === activeJobId;
                        const card = document.createElement("div");
                        card.className = `job-card ${isSelected ? 'active' : ''}`;
                        card.onclick = () => selectJob(job.job_id);
                        
                        card.innerHTML = `
                            <div class="job-header">
                                <span class="job-id">${job.job_id}</span>
                                <span class="status-badge status-${job.status}">${job.status}</span>
                            </div>
                            <div class="job-details">
                                <span class="job-time">${formatTime(job.started_at)}</span>
                            </div>
                        `;
                        container.appendChild(card);
                    });
                    
                    // If we have an active job, poll its details
                    if (activeJobId) {
                        pollActiveJob();
                    }
                } catch (err) {
                    console.error("Error fetching jobs:", err);
                }
            }

            async function selectJob(jobId) {
                activeJobId = jobId;
                
                // Highlight active card
                document.querySelectorAll(".job-card").forEach(card => {
                    const cardId = card.querySelector(".job-id").innerText;
                    if (cardId === jobId) {
                        card.classList.add("active");
                    } else {
                        card.classList.remove("active");
                    }
                });

                document.getElementById("selected-job-id").innerText = `Job ID: ${jobId}`;
                await pollActiveJob();
            }

            async function pollActiveJob() {
                if (!activeJobId) return;
                try {
                    const response = await fetch(`/api/v1/jobs/${activeJobId}`);
                    const data = await response.json();
                    
                    // Update status label in preview
                    const statusVal = data.status || (data.header && data.header.status);
                    
                    const statusBadgeHtml = statusVal 
                        ? `<span class="status-badge status-${statusVal}">${statusVal}</span>`
                        : '';
                    document.getElementById("selected-job-status").innerHTML = statusBadgeHtml;
                    
                    // Render details
                    const viewer = document.getElementById("json-viewer");
                    viewer.textContent = JSON.stringify(data, null, 2);
                    
                    // If active job is still queued or running, poll it again in 3 seconds
                    if (statusVal === "queued" || statusVal === "running") {
                        setTimeout(pollActiveJob, 3000);
                    }
                } catch (err) {
                    console.error("Error polling active job details:", err);
                }
            }

            // Initial Load & Automatic Update Interval
            fetchJobs();
            setInterval(fetchJobs, 6000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)
