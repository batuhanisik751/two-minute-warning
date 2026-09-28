"use client"; // replaces the root layout when the layout itself fails

// Its own document (no global styles, no theme): plain, readable, no details of the error.
export default function GlobalError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", maxWidth: "36rem", margin: "4rem auto", padding: "0 1rem" }}>
        <title>Something went wrong · Two-Minute Warning</title>
        <h1>Something went wrong</h1>
        <p>The site could not be loaded. Please try again in a moment.</p>
        {error.digest ? <p style={{ fontFamily: "monospace", fontSize: "0.8rem" }}>Reference: {error.digest}</p> : null}
        <button type="button" onClick={() => retry()} style={{ minHeight: 44, padding: "0 1rem" }}>
          Try again
        </button>
      </body>
    </html>
  );
}
