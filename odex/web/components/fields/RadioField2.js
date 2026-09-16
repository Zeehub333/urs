// fields/RadioField2 — Material RadioField distinct v2
// unique: with leading icon — hash e262
export class RadioField2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field radiofield2 has-icon"><span class="material-symbols-outlined" style="position:absolute;left:12px;top:50%;transform:translateY(-50%)">person</span><input style="padding-left:36px" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label style="left:36px">'+(cfg.label||'RadioField2')+' — with leading icon</label></div>'; }
}