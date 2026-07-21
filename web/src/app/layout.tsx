import type { Metadata } from "next";
import "./globals.css";
import Providers from "@/components/Providers";
import Navigation from "@/components/Navigation";

export const metadata: Metadata = {
  title: "Listening Archive",
  description: "A living, searchable picture of your Spotify listening history.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased min-h-screen">
        <Providers>
          <Navigation />
          <main className="app-main">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
