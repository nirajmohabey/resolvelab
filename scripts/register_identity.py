"""Reviewed identity enrollment; never exposed as an HTTP registration route."""
import argparse
import os
from uuid import UUID
from sqlalchemy import text
from resolvelab.db import admin_engine

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--issuer',required=True)
parser.add_argument('--subject',required=True)
parser.add_argument('--tenant-id',type=UUID,required=True)
parser.add_argument('--actor-id',type=UUID,required=True)
parser.add_argument('--customer-subject',required=True)
args=parser.parse_args()
if admin_engine is None:raise SystemExit('ADMIN_DATABASE_URL is required for reviewed enrollment.')
expected_issuer=os.environ.get('OIDC_ISSUER')
if os.environ.get('AUTH_MODE')=='neon':
    from resolvelab.neon_auth import settings
    expected_issuer=settings()[1]
if args.issuer!=expected_issuer or not args.issuer.startswith('https://'):
    raise SystemExit('Issuer must exactly match the configured trusted OIDC_ISSUER.')
if not 1<=len(args.subject)<=300 or not 1<=len(args.customer_subject)<=200:
    raise SystemExit('Identity fields exceed supported lengths.')
with admin_engine.begin() as conn:
    conn.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"),{'tenant':str(args.tenant_id)})
    active=conn.execute(text('SELECT 1 FROM resolvelab.memberships WHERE tenant_id=:tenant AND actor_id=:actor AND active'),
                        {'tenant':args.tenant_id,'actor':args.actor_id}).first()
    if not active:raise SystemExit('Create/review the active organization membership before enrollment.')
    existing=conn.execute(text('SELECT tenant_id,actor_id,customer_subject,active FROM resolvelab.external_identities WHERE issuer=:issuer AND external_subject=:subject'),
                          {'issuer':args.issuer,'subject':args.subject}).mappings().first()
    if existing:
        if not existing['active']:raise SystemExit('Identity is revoked. Review reactivation explicitly instead of silently reenrolling it.')
        if (str(existing['tenant_id']),str(existing['actor_id']),existing['customer_subject'])!=(str(args.tenant_id),str(args.actor_id),args.customer_subject):
            raise SystemExit('Identity already maps to different verified context. Review revocation and remapping explicitly.')
    else:
        conn.execute(text('INSERT INTO resolvelab.external_identities(issuer,external_subject,tenant_id,actor_id,customer_subject) VALUES(:issuer,:subject,:tenant,:actor,:customer)'),
                     {'issuer':args.issuer,'subject':args.subject,'tenant':args.tenant_id,'actor':args.actor_id,'customer':args.customer_subject})
print('Verified external identity enrollment complete; no token or credential was printed.')
