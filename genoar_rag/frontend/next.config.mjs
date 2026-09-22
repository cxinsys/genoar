/**
 * Plain JavaScript, and typed by the comment above the object rather than by
 * the file extension.
 *
 * This was next.config.ts. Next reads its config at start as well as at build,
 * and a TypeScript config means it needs TypeScript to read it — so the
 * production image, having quite reasonably dropped the compiler after building
 * with it, fetched six hundred packages from the registry every time a
 * container started. Nine seconds, and a runtime dependency on npm being
 * reachable, for a fourteen-line file with one type annotation in it.
 *
 * The annotation is still here. It is just somewhere that costs nothing to
 * read.
 *
 * @type {import('next').NextConfig}
 */
const nextConfig = {
  // The header named the framework and its absence costs nothing. A scanner
  // that reads banners files it under system-management findings.
  poweredByHeader: false,
  // What a browser is told about every response. These are the ones that
  // hold without TLS; HSTS and a content-security policy belong to the
  // reverse proxy that terminates HTTPS in front of the public deployment,
  // and are set there.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
  async rewrites() {
    const apiUrl = process.env.API_URL ?? "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${apiUrl}/api/:path*` },
      { source: "/health", destination: `${apiUrl}/health` },
    ];
  },
};

export default nextConfig;
