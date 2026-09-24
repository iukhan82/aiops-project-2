import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  /** Comparable value for sorting; a column without one is not sortable. */
  sortValue?: (row: T) => string | number | null | undefined;
  numeric?: boolean;
  /** Keep the value on one line (a time, a username); the table scrolls sideways before it breaks them. */
  nowrap?: boolean;
}

interface Props<T> {
  caption: string;
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  /** Rows are one Tab stop; Up/Down move, Enter or Space selects. */
  selectable?: boolean;
  selectedKey?: string | null;
  onSelect?: (row: T) => void;
  empty?: ReactNode;
  defaultSort?: { key: string; direction: "asc" | "desc" };
  /** A scroll container needs a name so a keyboard user can tell what it is. */
  label?: string;
}

const cellClass = (column: { numeric?: boolean; nowrap?: boolean }) => (column.numeric ? "num" : column.nowrap ? "nowrap" : undefined);

function compare(a: string | number | null | undefined, b: string | number | null | undefined): number {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  return typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "en");
}

/** A real table: caption, scoped headers, sortable columns. It is also the accessible equivalent of maps and charts. */
export function DataTable<T>({ caption, columns, rows, rowKey, selectable = false, selectedKey = null, onSelect, empty, defaultSort, label }: Props<T>) {
  const [sort, setSort] = useState(defaultSort ?? null);
  const [active, setActive] = useState(0);
  const body = useRef<HTMLTableSectionElement>(null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((c) => c.key === sort.key);
    if (!column?.sortValue) return rows;
    const get = column.sortValue;
    return [...rows].sort((x, y) => (sort.direction === "asc" ? 1 : -1) * compare(get(x), get(y)));
  }, [rows, columns, sort]);

  const focusRow = (index: number) => {
    const next = Math.max(0, Math.min(sorted.length - 1, index));
    setActive(next);
    (body.current?.children[next] as HTMLElement | undefined)?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, row: T, index: number) => {
    if (!selectable) return;
    if (event.target !== event.currentTarget) return;
    if (event.key === "ArrowDown") focusRow(index + 1);
    else if (event.key === "ArrowUp") focusRow(index - 1);
    else if (event.key === "Home") focusRow(0);
    else if (event.key === "End") focusRow(sorted.length - 1);
    else if (event.key === "Enter" || event.key === " ") onSelect?.(row);
    else return;
    event.preventDefault();
  };

  const toggleSort = (key: string) =>
    setSort((current) => (current?.key === key ? { key, direction: current.direction === "asc" ? "desc" : "asc" } : { key, direction: "asc" }));

  if (sorted.length === 0 && empty) return <>{empty}</>;
  return (
    <div className="table-wrap" role="region" aria-label={label ?? caption} tabIndex={0}>
      <table className="table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => {
              const direction = sort?.key === column.key ? sort.direction : undefined;
              return (
                <th key={column.key} scope="col" className={column.numeric ? "num" : undefined} aria-sort={direction ? (direction === "asc" ? "ascending" : "descending") : column.sortValue ? "none" : undefined}>
                  {column.sortValue ? (
                    <button type="button" className="th-button" onClick={() => toggleSort(column.key)}>
                      {column.header}
                      <span aria-hidden="true" className="sort-mark">
                        {direction === "asc" ? " ▲" : direction === "desc" ? " ▼" : ""}
                      </span>
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody ref={body}>
          {sorted.map((row, index) => {
            const key = rowKey(row);
            const selected = selectedKey === key;
            return (
              <tr
                key={key}
                className={selected ? "selected" : undefined}
                aria-current={selected ? "true" : undefined}
                tabIndex={selectable ? (index === Math.min(active, sorted.length - 1) ? 0 : -1) : undefined}
                onKeyDown={(event) => onKeyDown(event, row, index)}
                onClick={selectable ? () => onSelect?.(row) : undefined}
                onFocus={selectable ? () => setActive(index) : undefined}
                data-row-key={key}
              >
                {columns.map((column, ci) =>
                  ci === 0 ? (
                    <th key={column.key} scope="row" className={cellClass(column)}>
                      {column.render(row)}
                    </th>
                  ) : (
                    <td key={column.key} className={cellClass(column)}>
                      {column.render(row)}
                    </td>
                  ),
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
