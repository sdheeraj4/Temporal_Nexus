const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
class Node {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.attrs={};}
 set textContent(s){this.value=String(s);this.children=[];}
 get textContent(){return this.value+this.children.map(c=>c.textContent).join(' ');}
 set innerHTML(s){throw Error('HTML injection');}
 append(...items){this.children.push(...items);}
 setAttribute(k,v){this.attrs[k]=String(v);}
 replaceChildren(){this.children=[];this.value='';}
}
const renderSource=fs.readFileSync(require('node:path').join(__dirname,'../frontend/report.js'),'utf8');
function render(report){
 const env={document:{createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)},URL};vm.createContext(env);vm.runInContext(renderSource,env);
 const container=new Node('div');env.renderIdentityReport(container,report);return container;
}
const report={identity_label:'Alex <script>alert(1)</script>',status:'insufficient',summary:'Review needed',supported_fields:0,supplied_fields:1,analyzed_sources:1,source_domains:1,
 supplied_context:{name:'Alex'},fields:[{field:'name',supplied_value:'Alex',status:'partial',explanation:'Page mention only',evidence:[{value:'Alex',evidence:'<img onerror=alert(1)>',source_url:'javascript:alert(1)',extraction_method:'text_match'}]}],reviewed_image_clues:[{clue_id:'clue-0001',category:'name',selected:true,original_text:'Al3x',corrected_text:'Alex'}],sources:[],limitations:['Not a probability'],method:'Deterministic'};
test('report preserves OCR and renders hostile evidence as text',()=>{
 const root=render(report);assert.match(root.textContent,/Original: Al3x/);assert.match(root.textContent,/Reviewed: Alex/);assert.match(root.textContent,/<img onerror/);
 assert.match(root.textContent,/Source link unavailable/);assert.match(root.textContent,/0 \/ 1/);
});
test('empty report explains lack of supplied fields without inventing findings',()=>{
 const root=render({...report,fields:[],reviewed_image_clues:[]});assert.match(root.textContent,/No supplied comparable identity fields were available/);assert.match(root.textContent,/No image clues used/);
});
test('connected profiles show backend verdict, missing fields, access and safe crosslink evidence',()=>{
 const profile={platform:'linkedin',url:'javascript:alert(1)',domain:'linkedin.com',display_name:'<script>bad</script>',username:null,access_status:'SOURCE_INACCESSIBLE',relationship:'profile_candidate',external_profile_links:[{relation_type:'explicit_profile_link',target_url:'javascript:bad()',evidence:'<img onerror=bad()>'}],source_evidence:[],source_metadata:{indexed_snippet:'Unverified indexed snippet'}};
 const root=render({...report,connected_profiles:[{profile,status:'conflicting',supporting:['name'],missing:['role'],conflicts:['Current organization differs'],rationale:'Review before association',evidence:[]}]});
 assert.match(root.textContent,/CONNECTED PUBLIC FOOTPRINT/);assert.match(root.textContent,/Direct page inaccessible/);assert.match(root.textContent,/Unresolved \/ conflicting candidates/);
 assert.match(root.textContent,/Current organization differs/);assert.match(root.textContent,/Source link unavailable/);assert.match(root.textContent,/<script>bad/);
});

test('report keeps connected-profile summary concise and clips evidence excerpts',()=>{
 const long='A'.repeat(900);
 const profile={platform:'github',url:'https://github.com/alex',domain:'github.com',display_name:'Alex',username:'alex',access_status:'PUBLIC_PAGE_ANALYZED',relationship:'profile_candidate',external_profile_links:[],source_evidence:[],source_metadata:{indexed_snippet:long},related_records:[]};
 const root=render({...report,status:'supported',connected_profiles:[{profile,status:'supported',supporting:['name','username'],missing:[],conflicts:[],rationale:'Signals align',evidence:[{value:'Alex',evidence:long,source_url:'https://github.com/alex',extraction_method:'metadata'}]}]});
 assert.match(root.textContent,/CONNECTED PUBLIC FOOTPRINT/);assert.match(root.textContent,/✓ name · ✓ username/);
 assert.doesNotMatch(root.textContent,/A{700}/);
});


test('report follows the six-part identity intelligence flow',()=>{
 const root=render({...report,status:'supported',summary:'Evidence aligns',connected_profiles:[]});
 const text=root.textContent;
 const parts=['01 · MOST SUPPORTED IDENTITY','02 · CONNECTED PUBLIC FOOTPRINT','03 · TEMPORAL REASONING','04 · RELATIONSHIP GRAPH','05 · EVIDENCE','06 · CONFLICTS & UNCERTAINTY'];
 const indexes=parts.map(v=>text.indexOf(v));
 assert.ok(indexes.every(v=>v>=0));
 assert.deepEqual(indexes,[...indexes].sort((a,b)=>a-b));
});

test('report renders ML assist, temporal timeline and evidence graph without calling it probability',()=>{
 const enriched={...report,status:'supported',connected_profiles:[],ml_assessment:{score:.83,band:'strong',explanation:'Synthetic baseline supports the association.'},
  temporal_assessment:{status:'consistent',summary:'Dated observations align.',timeline:[{year_label:'2025',title:'Event: Open Source Summit',evidence:'Event page dated 2025',source_url:'https://example.org/event'}],issues:[]},
  relationship_graph:{summary:'2 nodes, 1 edge',nodes:[{id:'identity-root',label:'Alex',type:'identity',status:'supported'},{id:'profile-a',label:'github: @alex',type:'profile',status:'supported',url:'https://github.com/alex'}],edges:[{id:'e1',source:'identity-root',target:'profile-a',relation:'public trace',status:'supported',evidence_count:1,source_urls:['https://github.com/alex']}]}};
 const root=render(enriched);
 assert.match(root.textContent,/0\.83 · strong ML assist score/);
 assert.match(root.textContent,/Timeline & consistency/);
 assert.match(root.textContent,/Open Source Summit/);
 assert.match(root.textContent,/Evidence-backed identity connections/);
 assert.match(root.textContent,/not a calibrated identity probability/);
});
