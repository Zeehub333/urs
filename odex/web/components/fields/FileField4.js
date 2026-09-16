// fields/FileField4 — Material FileField distinct v4
// unique: with error — hash 048c
export class FileField4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field filefield4"><input value="'+v+'" style="border-color:var(--md-error);border-width:2px" /><label style="color:var(--md-error)">'+(cfg.label||'FileField4')+' *</label><div style="font-size:11px;color:var(--md-error)">'+(cfg.error||'خطأ — with error')+'</div></div>'; }
}