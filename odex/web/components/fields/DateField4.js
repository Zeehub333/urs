// fields/DateField4 — Material DateField distinct v4
// unique: with error — hash 9f43
export class DateField4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field datefield4"><input value="'+v+'" style="border-color:var(--md-error);border-width:2px" /><label style="color:var(--md-error)">'+(cfg.label||'DateField4')+' *</label><div style="font-size:11px;color:var(--md-error)">'+(cfg.error||'خطأ — with error')+'</div></div>'; }
}