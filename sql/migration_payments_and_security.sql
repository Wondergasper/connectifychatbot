-- ==============================================================================
-- Connectify Nigeria: Payments, Escrow & Security Schema Updates
-- ==============================================================================

-- 1. Ensure wallet_transactions.reference has a UNIQUE constraint for idempotent payouts
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'wallet_transactions_reference_key'
  ) then
    alter table public.wallet_transactions 
      add constraint wallet_transactions_reference_key unique (reference);
  end if;
end;
$$;

-- 2. Payments Table for tracking Paystack transactions
create table if not exists public.payments (
  id uuid primary key default gen_random_uuid(),
  booking_id uuid references public.bookings(id) on delete set null,
  reference text not null unique,
  paystack_reference text,
  amount_kobo bigint not null,
  currency text not null default 'NGN',
  status text not null default 'initialized', -- initialized, paid, failed, refunded, released
  customer_email text,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default timezone('utc', now()),
  updated_at timestamptz default timezone('utc', now())
);

-- Index for fast lookup by reference and booking_id
create index if not exists idx_payments_reference on public.payments(reference);
create index if not exists idx_payments_booking_id on public.payments(booking_id);

-- 3. Mark Booking Paid Atomic RPC (Executed upon verified Paystack webhook)
create or replace function public.mark_booking_paid_atomic(
  p_booking_id uuid,
  p_reference text,
  p_amount_kobo bigint,
  p_currency text default 'NGN'
) returns jsonb
language plpgsql
security definer
as $$
declare
  v_booking public.bookings%rowtype;
  v_res jsonb;
begin
  -- Lock and fetch booking
  select * into v_booking from public.bookings where id = p_booking_id for update;
  if not found then
    raise exception 'Booking not found';
  end if;

  -- Update payment status to held (funds held in escrow) and booking status to confirmed
  update public.bookings
     set status = 'confirmed',
         payment_status = 'held',
         updated_at = timezone('utc', now())
   where id = p_booking_id;

  -- Upsert payment record
  insert into public.payments (
    booking_id, reference, amount_kobo, currency, status, updated_at
  ) values (
    p_booking_id, p_reference, p_amount_kobo, p_currency, 'paid', timezone('utc', now())
  )
  on conflict (reference) do update
     set status = 'paid',
         updated_at = timezone('utc', now());

  select to_jsonb(b) into v_res from public.bookings b where b.id = p_booking_id;
  return v_res;
end;
$$;
