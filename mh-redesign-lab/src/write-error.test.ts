import {it,expect} from 'vitest';
import {writeError} from './write-error';
it('preserves domain conflict without exposing payload input',async()=>{const error=await writeError(new Response(JSON.stringify({detail:{code:'SHOP_PREPARATION_FAILED',message:'Действие магазина не завершилось; прежние данные сохранены'}}),{status:409}));expect(error.message).toContain('прежние данные сохранены');expect(error.message).not.toContain('/api/');});
it('turns validation arrays into readable message without echoing private input',async()=>{const error=await writeError(new Response(JSON.stringify({detail:[{input:'private',msg:'error'}]}),{status:422}));expect(error.message).toBe('Проверьте заполненные поля. Изменение не сохранено.');});
it('keeps non-json failure honest',async()=>{expect((await writeError(new Response('bad proxy',{status:502}))).message).toContain('502');});
