"use client"; // error boundaries are client components

import Link from "next/link";

// No error message or stack here: in production Next already replaces a server error's
// message with a digest, and nothing from the database (least of all a connection string)
// may reach the page. The digest matches the server log line.
export default function ErrorPage({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <div className="mx-auto max-w-xl py-12 text-center">
      <h1 className="text-2xl font-bold">Something went wrong</h1>
      <p className="mt-2 text-muted">
        This page could not be loaded. The data may be updating, or the database may be waking up; trying again in a
        moment usually works.
      </p>
      {error.digest ? <p className="mt-2 font-mono text-xs text-muted">Reference: {error.digest}</p> : null}
      <div className="mt-6 flex justify-center gap-3">
        <button
          type="button"
          onClick={() => retry()}
          className="min-h-11 rounded-md bg-accent px-4 text-sm font-semibold text-on-accent"
        >
          Try again
        </button>
        <Link href="/" className="inline-flex min-h-11 items-center rounded-md border border-line px-4 text-sm">
          Home
        </Link>
      </div>
    </div>
  );
}
