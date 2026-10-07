import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  transpilePackages: ["@platform/discovery"],
  turbopack: {
    root: path.resolve(__dirname, "../.."),
  },
  allowedDevOrigins: [
    "localhost",
    "localhost:3000",
    "127.0.0.1",
    "127.0.0.1:3000",
    "*.localhost",
    "*.localhost:3000",
  ],
};

export default nextConfig;
