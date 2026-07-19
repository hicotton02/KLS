import Link from "next/link";
import { ArrowLeft } from "lucide-react";

export default function NotFound() {
  return (
    <main className="page-width page-main not-found">
      <p className="eyebrow">404</p>
      <h1>That record is not here.</h1>
      <p>The bill or coverage area may have moved, or it may not be loaded yet.</p>
      <Link className="primary-button" href="/"><ArrowLeft size={17} aria-hidden="true" /> Back to coverage</Link>
    </main>
  );
}
