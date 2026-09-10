const baseUrl = (process.argv[2] ?? "http://127.0.0.1:5177").replace(/\/$/, "");

const checks = [
  ["frontend", `${baseUrl}/`],
  ["session", `${baseUrl}/tenant-api/api/v1/session`],
  ["action-plan", `${baseUrl}/tenant-api/api/v1/action-plan/items`],
];

let failed = false;
for (const [name, url] of checks) {
  try {
    const response = await fetch(url, { headers: { accept: "application/json,text/html" } });
    const expected = name === "frontend" ? response.ok : response.ok && response.headers.get("content-type")?.includes("json");
    console.log(`${expected ? "OK" : "FAIL"} ${name} ${response.status} ${url}`);
    if (!expected) failed = true;
  } catch (error) {
    failed = true;
    console.log(`FAIL ${name} ${error instanceof Error ? error.message : String(error)}`);
  }
}

if (failed) process.exitCode = 1;
