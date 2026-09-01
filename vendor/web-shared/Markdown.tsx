// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** Renders React text nodes only, so model- and catalog-derived text stays inert. */

import { Fragment, type ReactNode } from "react";
import { QuotedAsData } from "./QuotedAsData";

const INLINE = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)/g;
const BULLET = /^\s*[-•]\s+/;
const QUOTE = /^\s*>\s?/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/;
const HORIZONTAL_RULE = /^\s*(-{3,}|\*{3,}|_{3,})\s*$/;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(INLINE).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={key} className="font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={key} className="rounded bg-(--well) px-1 font-mono text-[13px]">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={key}>{part}</Fragment>;
  });
}

function splitRow(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function Table({ rows, id }: { rows: string[]; id: string }) {
  const hasHeader = rows.length > 1 && TABLE_SEPARATOR.test(rows[1]);
  const header = hasHeader ? splitRow(rows[0]) : null;
  const body = (hasHeader ? rows.slice(2) : rows).map(splitRow);
  return (
    <div className="panel-scroll my-2 overflow-x-auto rounded-lg border border-(--line)">
      <table className="w-full border-collapse text-left text-[13px]">
        {header ? (
          <thead>
            <tr className="border-b border-(--line) bg-(--well)/70">
              {header.map((cell, i) => (
                <th key={i} className="px-2.5 py-1.5 text-[11px] font-bold uppercase tracking-wide text-(--ink-soft)">
                  {renderInline(cell, `${id}-h${i}`)}
                </th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {body.map((cells, r) => (
            <tr key={r} className="border-b border-(--line) last:border-b-0">
              {cells.map((cell, c) => (
                <td key={c} className={`px-2.5 py-1.5 text-(--ink) ${c > 0 ? "text-right font-mono" : ""}`}>
                  {renderInline(cell, `${id}-${r}-${c}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  let rows: string[] = [];
  let quote: string[] = [];

  const flush = (at: number) => {
    if (bullets.length) {
      const list = bullets;
      bullets = [];
      blocks.push(
        <ul key={`ul-${at}`} className="my-1 list-disc space-y-0.5 pl-5">
          {list.map((item, i) => (
            <li key={i}>{renderInline(item, `ul-${at}-${i}`)}</li>
          ))}
        </ul>,
      );
    }
    if (rows.length) {
      const table = rows;
      rows = [];
      blocks.push(<Table key={`t-${at}`} rows={table} id={`t-${at}`} />);
    }
    if (quote.length) {
      const lines = quote;
      quote = [];
      blocks.push(
        <div key={`q-${at}`} className="my-1.5">
          <blockquote className="border-l-2 border-(--line) pl-2.5 text-[13px] italic leading-relaxed text-(--ink-soft)">
            {lines.map((line, i) => (
              <p key={i}>{renderInline(line, `q-${at}-${i}`)}</p>
            ))}
          </blockquote>
          <QuotedAsData subject="Quoted message" className="mt-1 pl-2.5" />
        </div>,
      );
    }
  };

  text.split("\n").forEach((line, index) => {
    if (TABLE_ROW.test(line)) {
      if (!rows.length) flush(index);
      rows.push(line);
    } else if (BULLET.test(line)) {
      if (!bullets.length) flush(index);
      bullets.push(line.replace(BULLET, ""));
    } else if (QUOTE.test(line)) {
      if (!quote.length) flush(index);
      quote.push(line.replace(QUOTE, ""));
    } else {
      flush(index);
      if (HORIZONTAL_RULE.test(line)) {
        blocks.push(<hr key={`hr-${index}`} className="my-3 border-t border-(--line)" />);
      } else if (line.trim() === "") {
        blocks.push(<div key={`sp-${index}`} className="h-2" />);
      } else {
        blocks.push(<p key={`p-${index}`}>{renderInline(line, `p-${index}`)}</p>);
      }
    }
  });
  flush(-1);
  return <>{blocks}</>;
}
