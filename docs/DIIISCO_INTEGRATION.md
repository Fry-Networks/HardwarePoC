This document defines how to deploy, secure, and operate the DIIISCO node in a fully autonomous, CPU‑only, GUI‑controlled environment.
It includes:

Docker architecture
Secret‑injection workflow (no .env file)
Auto‑model download
Healthchecks
Monitoring
Windows service design
GUI ↔ Service communication
Operational flow

# Overview
The DIIISCO node runs inside a Docker stack composed of:
- Ollama (CPU‑only) — runs the local LLM
- DIIISCO Node container — connects to Ollama and the Algorand network

The system is designed to:
- run on low‑spec PCs
- auto‑download the required LLM model
- auto‑restart on failure
- expose health and monitoring endpoints
- keep Algorand credentials hidden from the user
- be controlled by a GUI through a UAC‑elevated background service

# Architecture Diagram
+-------------------+         +---------------------------+
|       GUI         | <-----> |  Windows Service (UAC)    |
| (User Interface)  |         | - decrypts secrets        |
+-------------------+         | - injects env vars        |
                              | - starts/stops Docker     |
                              | - exposes status to GUI   |
                              +-------------+-------------+
                                            |
                                            v
                              +---------------------------+
                              |     Docker Compose        |
                              |  (Ollama + DIIISCO Node)  |
                              +---------------------------+

# Docker Compose Stack
docker-compose.yml
    version: "3.9"
    services:
    ollama:
        image: ollama/ollama:latest
        container_name: ollama
        restart: unless-stopped
        environment:
        OLLAMA_CPU_ONLY: "1"
        volumes:
        - ollama_models:/root/.ollama
        ports:
        - "11434:11434"
        healthcheck:
        test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
        interval: 30s
        timeout: 5s
        retries: 5

    diiisco:
        build: ./diiisco
        container_name: diiisco-node
        restart: unless-stopped
        depends_on:
        ollama:
            condition: service_healthy
        environment:
        ALGO_ADDRESS: "${ALGO_ADDRESS}"
        ALGO_MNEMONIC: "${ALGO_MNEMONIC}"

        OLLAMA_HOST: "http://ollama:11434"
        MODEL_NAME: "phi2"

        ENABLE_MONITORING: "true"
        MONITOR_INTERVAL: "30"
        ports:
        - "3000:3000"
        - "3001:3001"
        volumes:
        - diiisco_data:/app/data

    volumes:
    ollama_models:
    diiisco_data:

Key points
- No .env file — secrets are injected at runtime
- CPU‑only Ollama
- Healthcheck ensures DIIISCO starts only after Ollama is ready
- Monitoring endpoint exposed on port 3001

# DIIISCO Node Container
Dockerfile (diiisco/Dockerfile)
    FROM node:22

    WORKDIR /app

    COPY package*.json ./
    RUN npm install

    COPY . .

    CMD ["sh", "-c", "node src/index.js"]

Node Entrypoint (diiisco/src/index.js)

    import { execSync } from "child_process";
    import express from "express";
    import fs from "fs";

    const MODEL = process.env.MODEL_NAME || "phi2";
    const OLLAMA = process.env.OLLAMA_HOST || "http://ollama:11434";

    console.log(`Checking model: ${MODEL}`);
    try {
    execSync(`curl -s ${OLLAMA}/api/tags | grep ${MODEL}`, { stdio: "ignore" });
    console.log("Model already installed.");
    } catch {
    console.log(`Downloading model ${MODEL}...`);
    execSync(`curl -X POST ${OLLAMA}/api/pull -d '{"name":"${MODEL}"}'`);
    }

    console.log("Starting DIIISCO node...");

    setInterval(() => {
    fs.writeFileSync("/app/data/health.json", JSON.stringify({
        status: "ok",
        timestamp: Date.now()
    }, null, 2));
    }, 5000);

    if (process.env.ENABLE_MONITORING === "true") {
    const app = express();
    app.get("/stats", (req, res) => {
        res.json({
        cpu: process.cpuUsage(),
        memory: process.memoryUsage(),
        uptime: process.uptime(),
        model: MODEL,
        timestamp: Date.now()
        });
    });

    app.listen(3001, () => console.log("Monitoring on port 3001"));
    }

    console.log("Node running.");

This script:
- auto‑downloads the LLM model
- writes a health.json file
- exposes /stats for monitoring
- runs entirely on CPU

# Secret Injection (No .env File)
Secrets are:
- embedded encrypted inside the EXE
- decrypted only by the service
- injected into Docker as in‑memory environment variables

Example service command (use 1password to get the paramaters at build time):
    $env:ALGO_ADDRESS = "<decrypted_address>"
    $env:ALGO_MNEMONIC = "<decrypted_mnemonic>"

    docker compose -f "C:\Path\To\diiisco-node\docker-compose.yml" up -d

- Nothing is written to disk
- No .env, no temp files, no leaks.


The service:
- decrypts secrets in memory
- injects them into Docker
- starts/stops the node through ops requests coming from the GUI
- exposes status to the GUI

# GUI ↔ Service Communication
The GUI never handles secrets.
It only sends commands and receives status.

Recommended: Named Pipes
Messages from GUI → Service
    {"type":"start_node"}
    {"type":"stop_node"}
    {"type":"get_status"}

Messages from Service → GUI
    {"type":"status","running":true,"health":"ok","uptime":1234}
    {"type":"error","message":"Docker not installed"}

# Monitoring & Healthchecks
Health file (written by container)
    /app/data/health.json
    {
    "status": "ok",
    "timestamp": 1739999999999
    }
Monitoring endpoint
    http://localhost:3001/stats
Returns:
- CPU usage
- RAM usage
- uptime
- model name
- timestamp

The service can poll this and forward a simplified version to the GUI.

# Operational Flow
- User toggle the Diiisco toggle in GUI
- GUI sends start_node to service
- Service decrypts Algorand secrets
- Service injects them into Docker environment
- Service runs docker compose up -d
- Ollama starts → downloads model if needed
- DIIISCO starts after Ollama healthcheck
- Service polls /stats and health.json
- GUI displays node status and performance

# Summary
This setup provides:
- Full autonomy
- High security (no secrets on disk)
- Low hardware requirements
- Automatic model download
- Automatic restart
- Health & monitoring endpoints
- Clean GUI integration
- User isolation from Algorand credentials