/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output produces a self-contained server bundle that can be
  // copied into a slim Docker image (~150 MB final image vs ~600 MB for a
  // full node_modules tree).  Cloud Run + the GCP deployment workflow
  // depend on this.
  output: "standalone",
};

export default nextConfig;
