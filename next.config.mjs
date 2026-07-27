/** @type {import('next').NextConfig} */
const nextConfig = {
  serverExternalPackages: ["googleapis", "exceljs", "cheerio", "@google-cloud/bigquery"],
};
export default nextConfig;
