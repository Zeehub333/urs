// fields/FileField1 — Material FileField distinct v1
// unique: basic — hash 8650
export class FileField1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field filefield1"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'FileField1')+' — basic</label><div style="font-size:11px;color:#666">'+(cfg.helper||'basic')+'</div></div>'; }
}