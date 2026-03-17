from __future__ import annotations

import html


def render_cms1500_html(record: dict) -> str:
    snapshot = record.get("claim_snapshot", {})
    patient = snapshot.get("patient", {})
    provider = snapshot.get("provider", {})
    insurance = snapshot.get("insurance", {})
    address = patient.get("address", {})
    diagnosis_codes = snapshot.get("diagnosis_codes", [])
    service_lines = snapshot.get("service_lines", [])

    diagnosis_markup = "".join(
        f'<div class="diag-box">{html.escape(str(code))}</div>' for code in diagnosis_codes[:4]
    ) or '<div class="diag-box empty">N/A</div>'

    line_rows = []
    for line in service_lines[:6]:
        line_rows.append(
            "<tr>"
            f"<td>{html.escape(str(snapshot.get('service_date', '')))}</td>"
            f"<td>{html.escape(str(line.get('procedure_code', '')))}</td>"
            f"<td>{html.escape(str(line.get('diagnosis_pointer', '')))}</td>"
            f"<td>{html.escape(str(line.get('units', '')))}</td>"
            f"<td>${float(line.get('unit_price', 0)):.2f}</td>"
            f"<td>${float(line.get('charge_amount', 0)):.2f}</td>"
            "</tr>"
        )

    if not line_rows:
        line_rows.append('<tr><td colspan="6">No hay lineas de servicio registradas.</td></tr>')

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>CMS-1500 {html.escape(str(record.get('claim_id', '')))}</title>
  <style>
    body {{
      margin: 0;
      padding: 28px;
      font-family: Arial, sans-serif;
      background: #f6f8fb;
      color: #0f1720;
    }}
    .sheet {{
      max-width: 1050px;
      margin: 0 auto;
      background: white;
      border: 1px solid #d9e2ec;
      box-shadow: 0 18px 40px rgba(15, 23, 32, 0.08);
      padding: 28px;
    }}
    .header {{
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: end;
      gap: 12px;
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0;
      font-size: 32px;
    }}
    .sub {{
      margin: 4px 0 0;
      color: #526173;
      font-size: 14px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-bottom: 18px;
    }}
    .box {{
      border: 1px solid #d5dee8;
      border-radius: 10px;
      padding: 14px;
      min-height: 110px;
    }}
    .box h2 {{
      margin: 0 0 10px;
      font-size: 14px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #4f5d6d;
    }}
    .row {{
      margin: 0 0 6px;
      font-size: 14px;
    }}
    .diag-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-bottom: 18px;
    }}
    .diag-box {{
      border: 1px solid #d5dee8;
      border-radius: 10px;
      padding: 14px;
      font-weight: 700;
      min-height: 48px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .diag-box.empty {{
      color: #7b8794;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      border: 1px solid #d5dee8;
      padding: 10px;
      text-align: left;
      font-size: 14px;
    }}
    th {{
      background: #eef4fb;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-size: 12px;
      color: #536577;
    }}
    .totals {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
    }}
    .total-box {{
      border: 1px solid #d5dee8;
      border-radius: 10px;
      padding: 14px 16px;
      font-weight: 700;
      font-size: 18px;
    }}
    .actions {{
      margin-top: 18px;
      display: flex;
      gap: 10px;
    }}
    .actions a {{
      display: inline-block;
      background: #1e7fce;
      color: white;
      padding: 10px 14px;
      border-radius: 999px;
      text-decoration: none;
      font-size: 14px;
    }}
    @media print {{
      body {{ background: white; padding: 0; }}
      .sheet {{ box-shadow: none; border: 0; }}
      .actions {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="sheet">
    <div class="header">
      <div>
        <h1>CMS-1500</h1>
        <p class="sub">Vista imprimible del claim {html.escape(str(record.get('claim_id', '')))}</p>
      </div>
      <div><strong>Estatus:</strong> {html.escape(str(record.get('status', 'pending')).upper())}</div>
    </div>

    <div class="grid">
      <section class="box">
        <h2>Paciente</h2>
        <p class="row"><strong>Nombre:</strong> {html.escape(str(record.get('patient_name', '')))}</p>
        <p class="row"><strong>Member ID:</strong> {html.escape(str(record.get('member_id', '')))}</p>
        <p class="row"><strong>Fecha de nacimiento:</strong> {html.escape(str(patient.get('birth_date', '')))}</p>
        <p class="row"><strong>Genero:</strong> {html.escape(str(patient.get('gender', '')))}</p>
        <p class="row"><strong>Direccion:</strong> {html.escape(str(address.get('line1', '')))}, {html.escape(str(address.get('city', '')))}, {html.escape(str(address.get('state', '')))} {html.escape(str(address.get('zip_code', '')))}</p>
      </section>
      <section class="box">
        <h2>Seguro y Provider</h2>
        <p class="row"><strong>Payer:</strong> {html.escape(str(record.get('payer_name', '')))}</p>
        <p class="row"><strong>Payer ID:</strong> {html.escape(str(insurance.get('payer_id', '')))}</p>
        <p class="row"><strong>Policy #:</strong> {html.escape(str(insurance.get('policy_number', '')))}</p>
        <p class="row"><strong>Provider:</strong> {html.escape(str(provider.get('organization_name') or provider.get('first_name', '')))} {html.escape(str(provider.get('last_name', '')))}</p>
        <p class="row"><strong>NPI:</strong> {html.escape(str(provider.get('npi', '')))}</p>
      </section>
    </div>

    <div class="diag-grid">{diagnosis_markup}</div>

    <table>
      <thead>
        <tr>
          <th>Fecha servicio</th>
          <th>CPT / HCPCS</th>
          <th>DX Ptr</th>
          <th>Units</th>
          <th>Precio unit.</th>
          <th>Cargo</th>
        </tr>
      </thead>
      <tbody>{''.join(line_rows)}</tbody>
    </table>

    <div class="totals">
      <div>
        <div><strong>Tracking ID:</strong> {html.escape(str(record.get('tracking_id', '')))}</div>
        <div><strong>Claim # del payer:</strong> {html.escape(str(record.get('payer_claim_number', '')) or 'Pendiente')}</div>
      </div>
      <div class="total-box">Total Claim: ${float(record.get('total_charge_amount', 0)):.2f}</div>
    </div>

    <div class="actions">
      <a href="javascript:window.print()">Imprimir</a>
      <a href="/">Volver al portal</a>
    </div>
  </div>
</body>
</html>
"""
