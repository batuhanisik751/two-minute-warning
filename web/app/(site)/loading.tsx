// Shown in <main> while a page's queries run (the database may be waking up). Not used by
// /player/[id], which must answer an unknown id with a real 404 status before streaming.
export default function Loading() {
  return (
    <div role="status" aria-live="polite" className="animate-pulse">
      <div className="h-8 w-2/3 max-w-md rounded bg-raised" aria-hidden="true" />
      <p className="mt-4 text-sm text-muted">Loading the latest data&hellip;</p>
      <div className="mt-6 space-y-3" aria-hidden="true">
        <div className="h-4 w-full rounded bg-raised" />
        <div className="h-4 w-11/12 rounded bg-raised" />
        <div className="h-4 w-4/5 rounded bg-raised" />
        <div className="mt-6 h-48 w-full rounded border border-line bg-surface" />
      </div>
    </div>
  );
}
