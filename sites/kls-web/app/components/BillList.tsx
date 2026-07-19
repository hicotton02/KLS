import Link from "next/link";
import { ArrowRight } from "lucide-react";
import type { BillSummary } from "../lib/kls";
import { billHref } from "../lib/kls";

function displayDate(value: string | null) {
  if (!value) return "Date unavailable";
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return value;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(
    new Date(`${match[1]}-${match[2]}-${match[3]}T00:00:00Z`),
  );
}

function outcomeClass(outcome: string | null) {
  return ["active", "passed", "failed", "replaced"].includes(outcome ?? "") ? outcome : "active";
}

export function BillList({ bills, emptyMessage }: { bills: BillSummary[]; emptyMessage: string }) {
  if (!bills.length) return <div className="empty-state"><p>{emptyMessage}</p></div>;

  return (
    <div className="bill-list">
      {bills.map((bill) => (
        <article className="bill-row" key={`${bill.area_slug}-${bill.year}-${bill.bill_num}-${bill.special_session ?? "regular"}`}>
          <div className="bill-row-meta">
            <span>{bill.area_name}</span>
            <span>{bill.year}</span>
            <span>{displayDate(bill.last_action_date)}</span>
          </div>
          <div className="bill-row-body">
            <div>
              <div className="bill-title-line">
                <Link href={billHref(bill)}>{bill.bill_num}</Link>
                <span className={`status status-${outcomeClass(bill.outcome)}`}>{bill.status_label ?? "Status pending"}</span>
              </div>
              <h3>{bill.plain_language_title || bill.catch_title || bill.bill_title || "Untitled bill"}</h3>
              {bill.summary ? <p>{bill.summary}</p> : null}
              {bill.tags.length ? (
                <div className="tag-row">
                  {bill.tags.slice(0, 4).map((tag) => <span key={tag.value}>{tag.label}</span>)}
                </div>
              ) : null}
            </div>
            <Link className="row-arrow" href={billHref(bill)} aria-label={`Open ${bill.bill_num}`}>
              <ArrowRight size={20} aria-hidden="true" />
            </Link>
          </div>
        </article>
      ))}
    </div>
  );
}
