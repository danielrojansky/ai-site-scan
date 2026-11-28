-- AI Site Scan v3: accounts, credits from Polar orders, saved reports.
-- The browser reads its own rows (row level security). Only the server (secret key)
-- writes, through the functions at the end.

create table public.grants (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  plan text not null check (plan in ('single', 'pro')),
  polar_order_id text not null unique,
  polar_subscription_id text,
  reports_total int not null check (reports_total >= 0),
  reports_used int not null default 0 check (reports_used >= 0),
  valid_until timestamptz,                 -- pro: end of the paid month; single: null (no expiry)
  revoked_at timestamptz,                  -- refund
  created_at timestamptz not null default now()
);
create index grants_user_idx on public.grants (user_id);

-- One site unlocked with one credit. single: 1 rescan within 30 days; pro: rescans while the month is paid.
create table public.unlocks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  grant_id uuid not null references public.grants(id) on delete cascade,
  host text not null,
  rescans_left int,                        -- null = no limit (pro)
  rescans_until timestamptz not null,
  created_at timestamptz not null default now(),
  unique (user_id, grant_id, host)
);
create index unlocks_user_host_idx on public.unlocks (user_id, host);
create index unlocks_grant_idx on public.unlocks (grant_id);

create table public.reports (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  unlock_id uuid references public.unlocks(id) on delete set null,
  host text not null,
  url text not null,
  scan_key text not null,                  -- one scan result: claiming it twice uses one credit
  kind text not null check (kind in ('full', 'rescan')),
  score int,
  summary jsonb,
  guide_md text,
  emailed_at timestamptz,
  created_at timestamptz not null default now(),
  unique (user_id, host, scan_key)
);
create index reports_user_idx on public.reports (user_id, created_at desc);
create index reports_unlock_idx on public.reports (unlock_id);

create table public.webhook_events (
  id text primary key,
  type text not null,
  received_at timestamptz not null default now()
);

alter table public.grants enable row level security;
alter table public.unlocks enable row level security;
alter table public.reports enable row level security;
alter table public.webhook_events enable row level security;

create policy "own grants" on public.grants for select to authenticated using ((select auth.uid()) = user_id);
create policy "own unlocks" on public.unlocks for select to authenticated using ((select auth.uid()) = user_id);
create policy "own reports" on public.reports for select to authenticated using ((select auth.uid()) = user_id);

-- Credits from a paid Polar order. Repeats of the same order do nothing.
create or replace function public.grant_order(
  p_user uuid, p_plan text, p_order text, p_subscription text, p_total int, p_valid_until timestamptz
) returns uuid language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  insert into public.grants (user_id, plan, polar_order_id, polar_subscription_id, reports_total, valid_until)
  values (p_user, p_plan, p_order, p_subscription, p_total, p_valid_until)
  on conflict (polar_order_id) do nothing
  returning id into v_id;
  return v_id;
end $$;

-- Refund: the order's credits and sites stop working.
create or replace function public.revoke_order(p_order text) returns void
language sql security definer set search_path = '' as $$
  update public.grants set revoked_at = now() where polar_order_id = p_order and revoked_at is null;
$$;

-- Unlock one scan result of a site. Order of tries:
--   1. this scan was already unlocked: return it (no new credit)
--   2. a rescan on a site already unlocked (single: 1 within 30 days; pro: while the month is paid)
--   3. a new credit, from the grant that runs out first
-- status: 'ok' or 'no_credit'.
create or replace function public.claim_report(p_user uuid, p_host text, p_url text, p_scan_key text)
returns table (report_id uuid, kind text, plan text, status text)
language plpgsql security definer set search_path = '' as $$
#variable_conflict use_column
declare r public.reports; u public.unlocks; g public.grants; v_kind text;
begin
  perform pg_advisory_xact_lock(hashtext(p_user::text));

  select * into r from public.reports x where x.user_id = p_user and x.host = p_host and x.scan_key = p_scan_key;
  if found then
    select gg.plan into plan from public.unlocks uu join public.grants gg on gg.id = uu.grant_id where uu.id = r.unlock_id;
    return query select r.id, r.kind, plan, 'ok'::text; return;
  end if;

  select uu.* into u from public.unlocks uu join public.grants gg on gg.id = uu.grant_id
   where uu.user_id = p_user and uu.host = p_host and gg.revoked_at is null
     and uu.rescans_until > now() and (uu.rescans_left is null or uu.rescans_left > 0)
   order by uu.rescans_left is null desc, uu.rescans_until desc limit 1;
  if found then
    if u.rescans_left is not null then
      update public.unlocks set rescans_left = rescans_left - 1 where id = u.id;
    end if;
    v_kind := 'rescan';
  else
    select gg.* into g from public.grants gg
     where gg.user_id = p_user and gg.revoked_at is null and gg.reports_used < gg.reports_total
       and (gg.valid_until is null or gg.valid_until > now())
     order by gg.valid_until is null, gg.valid_until, gg.created_at limit 1;
    if not found then
      return query select null::uuid, null::text, null::text, 'no_credit'::text; return;
    end if;
    update public.grants set reports_used = reports_used + 1 where id = g.id;
    insert into public.unlocks (user_id, grant_id, host, rescans_left, rescans_until)
    values (p_user, g.id, p_host,
            case when g.plan = 'single' then 1 else null end,
            case when g.plan = 'single' then now() + interval '30 days' else g.valid_until end)
    returning * into u;
    v_kind := 'full';
  end if;

  insert into public.reports (user_id, unlock_id, host, url, scan_key, kind)
  values (p_user, u.id, p_host, p_url, p_scan_key, v_kind) returning * into r;
  select gg.plan into plan from public.grants gg where gg.id = u.grant_id;
  return query select r.id, v_kind, plan, 'ok'::text;
end $$;

revoke all on function public.grant_order(uuid, text, text, text, int, timestamptz) from public, anon, authenticated;
revoke all on function public.revoke_order(text) from public, anon, authenticated;
revoke all on function public.claim_report(uuid, text, text, text) from public, anon, authenticated;
grant execute on function public.grant_order(uuid, text, text, text, int, timestamptz) to service_role;
grant execute on function public.revoke_order(text) to service_role;
grant execute on function public.claim_report(uuid, text, text, text) to service_role;
