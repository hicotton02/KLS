"use client";

import { Send } from "lucide-react";
import { FormEvent, useState } from "react";

type MessageKind = "general" | "correction" | "advertising";

export function ContactForm({ initialKind = "general" }: { initialKind?: MessageKind }) {
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("sending");
    setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const payload = {
      kind: form.get("kind"),
      name: form.get("name"),
      email: form.get("email"),
      message: form.get("message"),
      website: form.get("website"),
      page_url: window.location.href,
    };

    try {
      const response = await fetch("/api/v1/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || "The message could not be sent.");
      }
      formElement.reset();
      setStatus("sent");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The message could not be sent.");
      setStatus("error");
    }
  }

  if (status === "sent") {
    return (
      <div className="contact-success" role="status">
        <strong>Message received.</strong>
        <p>Thank you. The site owner can now review it.</p>
        <button type="button" onClick={() => setStatus("idle")}>Send another message</button>
      </div>
    );
  }

  return (
    <form className="contact-form" onSubmit={submit}>
      <div className="contact-form-row">
        <label>
          <span>What is this about?</span>
          <select name="kind" defaultValue={initialKind}>
            <option value="general">General question</option>
            <option value="correction">Possible correction</option>
            <option value="advertising">Advertising</option>
          </select>
        </label>
        <label>
          <span>Your name <small>Optional</small></span>
          <input name="name" maxLength={120} autoComplete="name" />
        </label>
      </div>
      <label>
        <span>Email for a reply <small>Optional</small></span>
        <input name="email" type="email" maxLength={254} autoComplete="email" />
      </label>
      <label>
        <span>Message</span>
        <textarea name="message" minLength={10} maxLength={5000} rows={8} required placeholder="Include the bill number or page address when it matters." />
      </label>
      <label className="form-honeypot" aria-hidden="true">
        <span>Website</span>
        <input name="website" tabIndex={-1} autoComplete="off" />
      </label>
      <div className="contact-submit-row">
        <p>Do not send private legal records, passwords, or sensitive personal information.</p>
        <button className="primary-button" type="submit" disabled={status === "sending"}>
          <Send size={17} aria-hidden="true" /> {status === "sending" ? "Sending..." : "Send message"}
        </button>
      </div>
      {status === "error" ? <p className="form-error" role="alert">{error}</p> : null}
    </form>
  );
}
