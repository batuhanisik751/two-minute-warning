import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // web/ is its own npm project inside the Python repo: pin the Turbopack root here so a
  // lockfile higher up the tree is never taken as the workspace root.
  turbopack: { root: __dirname },
  // node-postgres uses Node-only APIs and must not be bundled (docs/deploy.md, "TLS").
  serverExternalPackages: ["pg"],
  poweredByHeader: false,
};

export default nextConfig;
