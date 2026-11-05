// ?stream=0 — без стриминга (Cloudflare Quick Tunnel не пропускает SSE), ?embed=1 — режим виджета.
export interface Params {
  stream: boolean;
  embed: boolean;
  siteKey: string | null;
  hostOrigin: string | null; // сайт с виджетом — адресат postMessage
}

export function readParams(search: string): Params {
  const p = new URLSearchParams(search);
  return {
    stream: p.get("stream") !== "0",
    embed: p.get("embed") === "1",
    siteKey: p.get("site_key"),
    hostOrigin: p.get("host_origin"),
  };
}
