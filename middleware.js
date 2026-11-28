// Vercel Routing Middleware: runs before static files.
// A request for / that asks for Markdown (Accept: text/markdown) gets /index.md as text/markdown.
// Every other request continues unchanged.
export const config = { matcher: "/" };

export default async function middleware(request) {
  const accept = request.headers.get("accept") || "";
  if (!/text\/markdown/i.test(accept)) {
    return new Response(null, { headers: { "x-middleware-next": "1" } });
  }
  const r = await fetch(new URL("/index.md", request.url));
  if (!r.ok) return new Response(null, { headers: { "x-middleware-next": "1" } });
  return new Response(await r.text(), {
    status: 200,
    headers: { "Content-Type": "text/markdown; charset=utf-8", "Vary": "Accept", "Cache-Control": "public, max-age=600" },
  });
}
