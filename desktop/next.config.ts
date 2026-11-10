import type { NextConfig } from "next";

// Electron 이 out/ 을 로컬 정적 서버로 띄운다 — 서버 기능 없이 정적 export.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
};

export default nextConfig;
