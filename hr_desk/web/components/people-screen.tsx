"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { useSession } from "@/components/session";
import { api, type EmployeeRow } from "@/lib/api";

import { ThemeSelect } from "@/components/theme-select";
import { DESIGNATIONS } from "@/lib/policy-roles";

const RETAIL = new Set([
  "salesman", "senior_store_manager", "store_manager", "assistant_store_manager",
  "branch_accountant", "regional_sales_manager", "area_sales_manager",
]);
const TEMPORARY = new Set(["intern", "contract", "trainee"]);
const PLAN_BY_ROLE: Record<string, string> = {
  coo_business: "a", director: "a", ceo: "a", cfo: "a", assistant_general_manager: "a",
  deputy_general_manager: "a", general_manager: "a", group_chief: "a", head_hr: "a",
  manager: "b", assistant_manager: "b", senior_manager: "b",
  executive: "c", senior_executive: "c", designer: "c",
  officer: "d", supervisor: "d", support_staff: "d",
  other_staff: "e",
};

function joinedLabel(value: string) {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function defaultsFor(role: string) {
  return {
    employeeType: RETAIL.has(role) ? "retail" as const : "head_office" as const,
    isPermanent: !TEMPORARY.has(role),
    medicalPlan: PLAN_BY_ROLE[role] ?? "",
  };
}

export function PeopleScreen() {
  const { me, retry } = useSession();
  const emailId = useId();
  const nameId = useId();
  const designationId = useId();
  const adminId = useId();
  const errorId = useId();
  const [people, setPeople] = useState<EmployeeRow[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const starting = defaultsFor("employee");
  const [designation, setDesignation] = useState("employee");
  const [isAdmin, setIsAdmin] = useState(false);
  const [employeeType, setEmployeeType] = useState(starting.employeeType);
  const [isPermanent, setIsPermanent] = useState(starting.isPermanent);
  const [medicalPlan, setMedicalPlan] = useState(starting.medicalPlan);
  const [joinedOn, setJoinedOn] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingRemove, setPendingRemove] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editRole, setEditRole] = useState("employee");
  const [editAdmin, setEditAdmin] = useState(false);
  const [editType, setEditType] = useState<"head_office" | "retail">("head_office");
  const [editPermanent, setEditPermanent] = useState(true);
  const [editPlan, setEditPlan] = useState("");
  const [editJoined, setEditJoined] = useState("");
  const [personQuery, setPersonQuery] = useState("");
  const [personRole, setPersonRole] = useState("All");
  const [personPlace, setPersonPlace] = useState("All");
  const [personSignedIn, setPersonSignedIn] = useState("All");
  const [personAdmin, setPersonAdmin] = useState("All");

  useEffect(() => {
    if (!me?.isAdmin) return;
    api<{ employees: EmployeeRow[] }>("/api/employees")
      .then((data) => setPeople(data.employees))
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Something went wrong. Try again."));
  }, [me?.isAdmin]);

  useEffect(() => {
    if (adding) document.getElementById(nameId)?.focus();
  }, [adding, nameId]);

  const personRoles = Array.from(new Set((people ?? []).map((person) => person.role).filter(Boolean))).sort((left, right) => left.localeCompare(right));
  const visiblePeople = (people ?? []).filter((person) => {
    const query = personQuery.trim().toLowerCase();
    const haystack = `${person.name} ${person.email} ${person.role}`.toLowerCase();
    const signedInOk = personSignedIn === "All"
      || (personSignedIn === "waiting" && !person.hasPassword)
      || (personSignedIn === "signed-in" && person.hasPassword);
    const adminOk = personAdmin === "All"
      || (personAdmin === "admin" && person.isAdmin)
      || (personAdmin === "not-admin" && !person.isAdmin);
    return (!query || haystack.includes(query))
      && (personRole === "All" || person.role === personRole)
      && (personPlace === "All" || person.place === personPlace)
      && signedInOk && adminOk;
  });
  const personFiltersOn = personQuery.trim() !== "" || personRole !== "All" || personPlace !== "All" || personSignedIn !== "All" || personAdmin !== "All";

  if (!me) {
    return (
      <section className="stack">
        <header className="page-head">
          <h1>People</h1>
          <p className="lead">Sign in to continue.</p>
        </header>
        <div className="actions">
          <Link className="primary" href="/sign-in">
            Sign in
          </Link>
        </div>
      </section>
    );
  }
  if (!me.isAdmin) {
    return (
      <section className="stack">
        <header className="page-head">
          <h1>People</h1>
          <p className="lead">Only an admin can open this page.</p>
        </header>
        <Link href="/">Back to Ask HR</Link>
      </section>
    );
  }

  const invalidEmail = error.startsWith("Enter a valid email");
  const invalidName = error.startsWith("Enter this person's");
  const invalidDesignation = error.startsWith("Choose a role");

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api<{ message: string; employee: EmployeeRow }>("/api/employees", {
        method: "POST",
        body: JSON.stringify({
          email, name, designation, is_admin: isAdmin,
          employee_type: employeeType, is_permanent: isPermanent,
          medical_plan: medicalPlan, joined_on: joinedOn,
        }),
      });
      setMessage(result.message);
      setAdding(false);
      setEmail("");
      setName("");
      setDesignation("employee");
      setIsAdmin(false);
      const reset = defaultsFor("employee");
      setEmployeeType(reset.employeeType);
      setIsPermanent(reset.isPermanent);
      setMedicalPlan(reset.medicalPlan);
      setJoinedOn("");
      const data = await api<{ employees: EmployeeRow[] }>("/api/employees");
      setPeople(data.employees);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="stack">
      <header className="page-head">
        <div className="section-heading">
          <h1>People</h1>
          {!adding ? <button className="primary" type="button" disabled={busy} onClick={() => {
            setAdding(true);
            setMessage("");
            setError("");
          }}>Add employee</button> : null}
        </div>
      </header>
      {error ? <p id={errorId} className="form-error" role="alert">{error}</p> : null}
      {message ? <p className="success" role="status">{message}</p> : null}
      {adding ? <form className="policy-form panel" onSubmit={onSubmit}>
        <h2>Add employee</h2>
        <div className="field">
          <label htmlFor={emailId}>Work email</label>
          <input
            id={emailId}
            type="email"
            autoComplete="off"
            required
            value={email}
            aria-invalid={invalidEmail || undefined}
            aria-describedby={invalidEmail ? errorId : undefined}
            onChange={(event) => setEmail(event.target.value)}
          />
          
        </div>
        <div className="field">
          <label htmlFor={nameId}>Name</label>
          <input
            id={nameId}
            required
            value={name}
            aria-invalid={invalidName || undefined}
            aria-describedby={invalidName ? errorId : undefined}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={designationId}>Job title</label>
          <ThemeSelect
            id={designationId}
            value={designation}
            ariaInvalid={invalidDesignation}
            ariaDescribedBy={invalidDesignation ? errorId : undefined}
            options={DESIGNATIONS.map(([value, label]) => ({ value, label }))}
            onChange={(role) => {
              const next = defaultsFor(role);
              setDesignation(role);
              setEmployeeType(next.employeeType);
              setIsPermanent(next.isPermanent);
              setMedicalPlan(next.medicalPlan);
            }}
          />
        </div>
        <WorkFields
          typeId={`${designationId}-type`}
          permanentId={`${designationId}-permanent`}
          planId={`${designationId}-plan`}
          joinedId={`${designationId}-joined`}
          employeeType={employeeType}
          isPermanent={isPermanent}
          medicalPlan={medicalPlan}
          joinedOn={joinedOn}
          onType={setEmployeeType}
          onPermanent={setIsPermanent}
          onPlan={setMedicalPlan}
          onJoined={setJoinedOn}
        />
        <div className="check-row person-check">
          <input
            id={adminId}
            type="checkbox"
            checked={isAdmin}
            onChange={(event) => setIsAdmin(event.target.checked)}
          />
          <div>
            <label htmlFor={adminId}>Admin access</label>
            <p className="person-hint">They can add people and update policies.</p>
          </div>
        </div>
        <div className="row-actions">
          <button className="primary" type="submit" disabled={busy}>{busy ? "Adding…" : "Add employee"}</button>
          <button type="button" disabled={busy} onClick={() => {
            setAdding(false);
            setEmail("");
            setName("");
            setDesignation("employee");
            setIsAdmin(false);
            const reset = defaultsFor("employee");
            setEmployeeType(reset.employeeType);
            setIsPermanent(reset.isPermanent);
            setMedicalPlan(reset.medicalPlan);
            setJoinedOn("");
            setError("");
          }}>Cancel</button>
        </div>
      </form> : null}
      {people === null ? (
        !error ? <p role="status">Loading people…</p> : null
      ) : people.length === 0 ? (
        <p className="empty-state">No employees added yet.</p>
      ) : (
        <>
        <div className="list-filters">
          <label>
            Search
            <input
              type="search"
              value={personQuery}
              placeholder="Name, email, or job title"
              onChange={(event) => setPersonQuery(event.target.value)}
            />
          </label>
          <label>
            Job title
            <ThemeSelect
              value={personRole}
              onChange={setPersonRole}
              options={[{ value: "All", label: "All" }, ...personRoles.map((role) => ({ value: role, label: role }))]}
            />
          </label>
          <label>
            Where they work
            <ThemeSelect
              value={personPlace}
              onChange={setPersonPlace}
              options={[
                { value: "All", label: "All" },
                { value: "Head office", label: "Head office" },
                { value: "Retail store", label: "Retail store" },
              ]}
            />
          </label>
          <label>
            Sign-in
            <ThemeSelect
              value={personSignedIn}
              onChange={setPersonSignedIn}
              options={[
                { value: "All", label: "All" },
                { value: "waiting", label: "Hasn't signed in yet" },
                { value: "signed-in", label: "Has signed in" },
              ]}
            />
          </label>
          <label>
            Admin access
            <ThemeSelect
              value={personAdmin}
              onChange={setPersonAdmin}
              options={[
                { value: "All", label: "All" },
                { value: "admin", label: "Admin access" },
                { value: "not-admin", label: "No admin access" },
              ]}
            />
          </label>
          {personFiltersOn ? (
            <button type="button" className="clear-filters" onClick={() => {
              setPersonQuery("");
              setPersonRole("All");
              setPersonPlace("All");
              setPersonSignedIn("All");
              setPersonAdmin("All");
            }}>Clear filters</button>
          ) : null}
        </div>
        <p className="list-filter-count">
          {visiblePeople.length === people.length
            ? `${people.length} ${people.length === 1 ? "person" : "people"}`
            : `${visiblePeople.length} of ${people.length} people`}
        </p>
        {visiblePeople.length === 0 ? (
          <p className="empty-state">No people match.</p>
        ) : (
        <ul className="plain-list people-list">
          {visiblePeople.map((person) => (
            <li key={person.email}>
              <strong>{person.name}</strong>
              <span className="file-name">{person.email}</span>
              <div className="person-facts">
                <span>{person.role}</span>
                {person.place ? <span>{person.place}</span> : null}
                <span>{person.isPermanent ? "Permanent" : "Not permanent"}</span>
                {person.medicalPlan ? <span>Medical plan {person.medicalPlan.toUpperCase()}</span> : null}
                {person.joinedOn ? <span>Joined {joinedLabel(person.joinedOn)}</span> : null}
                {person.isAdmin ? <span>Admin access</span> : null}
                {!person.hasPassword ? <span className="is-waiting">Hasn't signed in yet</span> : null}
              </div>
              {editing === person.email ? (
                <div className="person-edit">
                  <div className="field">
                    <label htmlFor={`role-${person.email}`}>Job title</label>
                    <ThemeSelect
                      id={`role-${person.email}`}
                      value={editRole}
                      options={DESIGNATIONS.map(([value, label]) => ({ value, label }))}
                      onChange={setEditRole}
                    />
                    <p className="person-hint">Their job title. This decides which policy answers they can see.</p>
                  </div>
                  <WorkFields
                    typeId={`type-${person.email}`}
                    permanentId={`permanent-${person.email}`}
                    planId={`plan-${person.email}`}
                    joinedId={`joined-${person.email}`}
                    employeeType={editType}
                    isPermanent={editPermanent}
                    medicalPlan={editPlan}
                    joinedOn={editJoined}
                    onType={setEditType}
                    onPermanent={setEditPermanent}
                    onPlan={setEditPlan}
                    onJoined={setEditJoined}
                  />
                  <div className="check-row person-check">
                    <input
                      id={`admin-${person.email}`}
                      type="checkbox"
                      checked={editAdmin}
                      onChange={(event) => setEditAdmin(event.target.checked)}
                    />
                    <div>
                      <label htmlFor={`admin-${person.email}`}>Admin access</label>
                      <p className="person-hint">They can add people and update policies.</p>
                    </div>
                  </div>
                  <div className="person-edit-actions">
                  <button
                    type="button"
                    className="primary"
                    disabled={busy}
                    onClick={() => {
                      setBusy(true);
                      setError("");
                      setMessage("");
                      api<{ message: string }>(`/api/employees/${encodeURIComponent(person.email)}`, {
                        method: "PATCH",
                        body: JSON.stringify({
                          designation: editRole, is_admin: editAdmin,
                          employee_type: editType, is_permanent: editPermanent,
                          medical_plan: editPlan, joined_on: editJoined,
                        }),
                      })
                        .then(async (result) => {
                          setMessage(result.message);
                          setEditing(null);
                          if (person.email === me.email) await retry();
                          const data = await api<{ employees: EmployeeRow[] }>("/api/employees");
                          setPeople(data.employees);
                        })
                        .catch((caught) =>
                          setError(caught instanceof Error ? caught.message : "Something went wrong. Try again."),
                        )
                        .finally(() => setBusy(false));
                    }}
                  >
                    Save changes
                  </button>
                  <button type="button" disabled={busy} onClick={() => setEditing(null)}>
                    Cancel
                  </button>
                  </div>
                </div>
              ) : person.email !== me.email && pendingRemove === person.email ? (
                <div className="row-actions">
                  <span>Remove {person.name}? They will no longer be able to sign in.</span>
                  <button
                    type="button"
                    className="danger"
                    disabled={busy}
                    onClick={() => {
                      setBusy(true);
                      setError("");
                      setMessage("");
                      api<{ message: string }>(`/api/employees/${encodeURIComponent(person.email)}`, {
                        method: "DELETE",
                      })
                        .then(async (result) => {
                          setMessage(result.message);
                          setPendingRemove(null);
                          const data = await api<{ employees: EmployeeRow[] }>("/api/employees");
                          setPeople(data.employees);
                        })
                        .catch((caught) =>
                          setError(caught instanceof Error ? caught.message : "Something went wrong. Try again."),
                        )
                        .finally(() => setBusy(false));
                    }}
                  >
                    Remove employee
                  </button>
                  <button type="button" disabled={busy} onClick={() => setPendingRemove(null)}>
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="row-actions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setEditing(person.email);
                      setEditRole(person.designation);
                      setEditAdmin(person.isAdmin);
                      setEditType(person.employeeType);
                      setEditPermanent(person.isPermanent);
                      setEditPlan(person.medicalPlan || "");
                      setEditJoined(person.joinedOn || "");
                      setPendingRemove(null);
                      setError("");
                      setMessage("");
                    }}
                  >
                    Edit
                  </button>
                  {person.email === me.email ? null : (
                    <button
                      type="button"
                      className="danger"
                      disabled={busy}
                      onClick={() => {
                        setPendingRemove(person.email);
                        setEditing(null);
                        setError("");
                        setMessage("");
                      }}
                    >
                      Remove
                    </button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
        )}
        </>
      )}
    </section>
  );
}

function WorkFields({
  typeId, permanentId, planId, joinedId,
  employeeType, isPermanent, medicalPlan, joinedOn,
  onType, onPermanent, onPlan, onJoined,
}: {
  typeId: string;
  permanentId: string;
  planId: string;
  joinedId: string;
  employeeType: "head_office" | "retail";
  isPermanent: boolean;
  medicalPlan: string;
  joinedOn: string;
  onType: (value: "head_office" | "retail") => void;
  onPermanent: (value: boolean) => void;
  onPlan: (value: string) => void;
  onJoined: (value: string) => void;
}) {
  return (
    <>
      <div className="field">
        <label htmlFor={typeId}>Where they work</label>
        <ThemeSelect
          id={typeId}
          value={employeeType}
          options={[
            { value: "head_office", label: "Head office" },
            { value: "retail", label: "Retail store" },
          ]}
          onChange={(value) => onType(value as "head_office" | "retail")}
        />
        <p className="person-hint">Head office or a retail store.</p>
      </div>
      <div className="field">
        <label htmlFor={planId}>Medical plan</label>
        <ThemeSelect
          id={planId}
          value={medicalPlan}
          options={[
            { value: "", label: "Not set" },
            ...["a", "b", "c", "d", "e"].map((letter) => ({ value: letter, label: `Plan ${letter.toUpperCase()}` })),
          ]}
          onChange={onPlan}
        />
        <p className="person-hint">Leave this as Not set if you are not sure.</p>
      </div>
      <div className="field">
        <label htmlFor={joinedId}>Joining date</label>
        <input id={joinedId} type="date" value={joinedOn} onChange={(event) => onJoined(event.target.value)} />
        <p className="person-hint">The day they joined. The provident fund uses this date.</p>
      </div>
      <div className="check-row person-check">
        <input id={permanentId} type="checkbox" checked={isPermanent} onChange={(event) => onPermanent(event.target.checked)} />
        <div>
          <label htmlFor={permanentId}>Permanent employee</label>
          <p className="person-hint">Untick this for an intern, a contract, or a trainee.</p>
        </div>
      </div>
    </>
  );
}
