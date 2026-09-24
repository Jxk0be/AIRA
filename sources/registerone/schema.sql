-- RegisterOne: a fictional cloud POS backend.
--
-- Shaped the way Square-style systems actually structure data, because that is
-- the shape our adapter has to survive: a catalog of items whose price lives on
-- the variation, per-location inventory counts, orders with line items, and
-- payments and refunds as separate objects.
--
-- This is a CUSTOMER SOURCE system. It is read-only to us and never goes into
-- Supabase. Money is integer cents, quantities are decimal strings, timestamps
-- are UTC — all three of those are conversions the adapter owes us.

create table locations (
    id          text primary key,          -- LOC_...
    name        text        not null,
    timezone    text        not null,
    status      text        not null       -- ACTIVE | INACTIVE
);

create table categories (
    id          text primary key,          -- CAT_...
    name        text        not null,
    parent_id   text references categories (id),
    updated_at  timestamptz not null
);

create table vendors (
    id              text primary key,      -- VEND_...
    name            text not null,
    account_number  text,
    email           text,
    phone           text,
    -- Free text, the way a real supplier record carries it. The returns policy
    -- lives in here rather than in a column because no POS has a column for it.
    notes           text
);

create table catalog_items (
    id                 text primary key,   -- ITEM_...
    name               text        not null,
    description        text,
    category_id        text references categories (id),
    product_type       text        not null,
    is_deleted         boolean     not null default false,
    custom_attributes  jsonb       not null default '{}'::jsonb,
    created_at         timestamptz not null,
    updated_at         timestamptz not null
);

-- Price lives here, not on the item. So do SKU, barcode and stock tracking.
create table item_variations (
    id               text primary key,     -- VAR_...
    item_id          text        not null references catalog_items (id),
    name             text,
    sku              text,
    upc              text,
    price_amount     bigint,               -- cents; null when pricing_type = VARIABLE
    currency         text        not null default 'USD',
    pricing_type     text        not null, -- FIXED | VARIABLE
    track_inventory  boolean     not null default true,
    is_deleted       boolean     not null default false,
    updated_at       timestamptz not null
);

-- Cost lives off to the side and is often simply absent.
create table variation_vendor_info (
    variation_id      text not null references item_variations (id),
    vendor_id         text not null references vendors (id),
    unit_cost_amount  bigint,              -- cents, nullable on purpose
    primary key (variation_id, vendor_id)
);

-- Current stock per variation per location, per state.
create table inventory_counts (
    variation_id   text        not null references item_variations (id),
    location_id    text        not null references locations (id),
    state          text        not null,   -- IN_STOCK | SOLD | WASTE
    quantity       text        not null,   -- decimal string, like the real API
    calculated_at  timestamptz not null,
    primary key (variation_id, location_id, state)
);

-- The history those counts are the sum of.
create table inventory_adjustments (
    id            text primary key,        -- ADJ_...
    variation_id  text        not null references item_variations (id),
    location_id   text        not null references locations (id),
    from_state    text,                    -- null for receiving
    to_state      text        not null,
    quantity      text        not null,
    occurred_at   timestamptz not null,
    reason        text
);

create table customers (
    id            text primary key,        -- CUST_...
    given_name    text,
    family_name   text,
    email         text,
    phone         text,
    reference_id  text,
    created_at    timestamptz not null,
    updated_at    timestamptz not null
);

create table orders (
    id                    text primary key, -- ORD_...
    location_id           text        not null references locations (id),
    customer_id           text references customers (id),
    state                 text        not null, -- OPEN | COMPLETED | CANCELED
    source                text        not null, -- POS | ONLINE
    total_money           bigint      not null,
    total_tax_money       bigint      not null,
    total_discount_money  bigint      not null,
    total_tip_money       bigint      not null,
    created_at            timestamptz not null,
    -- Not in the original RegisterOne spec, added because the API exposes an
    -- updated_at range filter and incremental sync is meaningless without one.
    updated_at            timestamptz not null,
    closed_at             timestamptz,
    version               int         not null default 1
);

create table order_line_items (
    uid                   text primary key,
    order_id              text        not null references orders (id),
    -- Null means a custom-amount sale rung up at the register, with no catalog
    -- object behind it.
    catalog_object_id     text references item_variations (id),
    name                  text        not null,
    variation_name        text,
    quantity              text        not null,  -- decimal string
    base_price_money      bigint      not null,
    total_discount_money  bigint      not null default 0,
    gross_sales_money     bigint      not null,
    total_money           bigint      not null,
    note                  text
);

create table payments (
    id            text primary key,        -- PAY_...
    order_id      text        not null references orders (id),
    amount_money  bigint      not null,
    tip_money     bigint      not null default 0,
    source_type   text        not null,    -- CARD | CASH | EXTERNAL
    card_brand    text,
    status        text        not null,    -- COMPLETED | CANCELED
    created_at    timestamptz not null
);

create table refunds (
    id            text primary key,        -- REF_...
    payment_id    text        not null references payments (id),
    order_id      text        not null references orders (id),
    amount_money  bigint      not null,
    reason        text,
    status        text        not null,
    created_at    timestamptz not null
);

-- Indexes the mock API's list endpoints need to paginate without a seq scan.
create index on orders (updated_at, id);
create index on orders (created_at);
create index on orders (location_id);
create index on order_line_items (order_id);
create index on inventory_adjustments (occurred_at, id);
create index on inventory_counts (location_id);
create index on payments (created_at, id);
create index on payments (order_id);
create index on refunds (created_at, id);
create index on customers (updated_at, id);
create index on item_variations (updated_at, id);
create index on item_variations (item_id);
create index on catalog_items (updated_at, id);
