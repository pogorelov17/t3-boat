// Request the same provider snapshot as the T3 v0.1.11 model picker.
import { readFileSync } from "node:fs";

const [port, instanceId] = process.argv.slice(2);
const origin = "http://127.0.0.1:" + port;

async function refreshProvider() {
  const bearer = readFileSync(0, "utf8").trim();
  const response = await fetch(origin + "/api/auth/websocket-ticket/rpc", {
    method: "POST",
    headers: { Authorization: "Bearer " + bearer },
    redirect: "error",
  });
  if (!response.ok) throw new Error("session rejected");
  const { ticket } = await response.json();
  const url = new URL("/ws", origin);
  url.protocol = "ws:";
  url.searchParams.set("wsTicket", ticket);
  url.searchParams.set("orchestrationProtocol", "2");
  const socket = new WebSocket(url);
  try {
    return await new Promise((resolve, reject) => {
      socket.onopen = () => socket.send(JSON.stringify({
        _tag: "Request", id: "1", tag: "server.refreshProviders", headers: [],
        payload: { instanceId, fresh: true, refreshModels: true, workspaceSnapshotsOnDemand: true },
      }));
      socket.onerror = socket.onclose = () => reject(new Error("socket closed"));
      socket.onmessage = ({ data }) => {
        try {
          const reply = JSON.parse(data);
          if (reply._tag !== "Exit" || reply.requestId !== "1") return;
          if (reply.exit._tag !== "Success") throw new Error("refresh failed");
          resolve(reply.exit.value);
        } catch (error) {
          reject(error);
        }
      };
    });
  } finally {
    socket.close();
  }
}

try {
  console.log(JSON.stringify(await refreshProvider()));
} catch {
  // Server responses and connection URLs can include private account information.
  process.exitCode = 1;
}
