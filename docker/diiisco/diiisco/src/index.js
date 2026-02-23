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
