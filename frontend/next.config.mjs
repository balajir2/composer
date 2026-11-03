import { buildSecurityHeaders } from "./lib/security-headers.mjs";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output produces a self-contained server bundle that can be
  // copied into a slim Docker image (~150 MB final image vs ~600 MB for a
  // full node_modules tree).  Cloud Run + the GCP deployment workflow
  // depend on this.
  output: "standalone",

  // Defence in depth: the app uses no next/image, so do not expose the Image
  // Optimization API (/_next/image) at all.  Several critical Next.js RCEs/DoS
  // advisories target that endpoint.
  images: { unoptimized: true },

  async headers() {
    return [{ source: "/:path*", headers: buildSecurityHeaders() }];
  },
};

export default nextConfig;
