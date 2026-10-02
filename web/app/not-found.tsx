import Link from "next/link";
import { EmptyState, PageHeader } from "@/components/ui";

export const metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <>
      <PageHeader title="Not found">There is no page at this address.</PageHeader>
      <EmptyState title="Nothing here">
        <p>
          A player link needs an nflverse player id (for example from a Waiver Radar list), and a coach link a
          head coach from the <Link href="/decisions">Decision Report Card</Link>. Try the{" "}
          <Link href="/waivers">Waivers page</Link> or go back to <Link href="/">this week</Link>.
        </p>
      </EmptyState>
    </>
  );
}
