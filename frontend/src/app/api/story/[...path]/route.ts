import crypto from "crypto";
import { headers } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";

const FORWARDED_RESPONSE_HEADERS = [
  "accept-ranges",
  "cache-control",
  "content-disposition",
  "content-length",
  "content-range",
  "content-type",
  "etag",
  "last-modified",
] as const;

function getStudioApiUrl(): string {
  return (process.env.STUDIO_API_URL?.trim() || "http://127.0.0.1:8190").replace(/\/$/, "");
}

function buildStudioProxyHeaders(userId: string, username: string): Record<string, string> {
  const secret = process.env.STUDIO_PROXY_SECRET?.trim();
  if (!secret) {
    throw new Error("STUDIO_PROXY_SECRET is not configured in frontend environment");
  }
  const ts = Math.floor(Date.now() / 1000).toString();
  const signature = crypto.createHmac("sha256", secret).update(`${userId}:${ts}`).digest("hex");
  return {
    "x-studio-user-id": userId,
    "x-studio-username": username,
    "x-studio-ts": ts,
    "x-studio-signature": signature,
  };
}

async function handleProxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ detail: "Authentication required" }, { status: 401 });
  }

  let proxyHeaders: Record<string, string>;
  try {
    const displayName = session.user.name?.trim() || session.user.email || session.user.id;
    proxyHeaders = buildStudioProxyHeaders(session.user.id, displayName);
  } catch (err) {
    return NextResponse.json(
      { detail: err instanceof Error ? err.message : "Proxy auth misconfigured" },
      { status: 500 },
    );
  }

  const { path } = await context.params;
  const upstreamPath = `/api/${path.map((segment) => encodeURIComponent(segment)).join("/")}`;
  const targetUrl = `${getStudioApiUrl()}${upstreamPath}${request.nextUrl.search}`;

  const upstreamHeaders = new Headers(proxyHeaders);
  const contentType = request.headers.get("content-type");
  if (contentType) upstreamHeaders.set("content-type", contentType);
  const range = request.headers.get("range");
  if (range) upstreamHeaders.set("range", range);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const body = hasBody ? await request.arrayBuffer() : undefined;

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method: request.method,
      headers: upstreamHeaders,
      body,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { detail: "Cannot reach studio_api on port 8190" },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const val = upstream.headers.get(name);
    if (val) responseHeaders.set(name, val);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return handleProxy(request, context);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return handleProxy(request, context);
}

export async function PUT(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return handleProxy(request, context);
}

export async function PATCH(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return handleProxy(request, context);
}

export async function DELETE(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return handleProxy(request, context);
}
