"""Synthetic inputs and review checklists. Generation is NOT semantic acceptance."""
from hashlib import sha256


def natural_case(profile, seed):
    if profile not in ('short', '10m'):
        raise ValueError('natural cases support short and 10m only')
    scenes = [
        ('植物观察角', '薄荷', '花盆', '新叶'),
        ('手工制作角', '纸艺', '作品', '折痕'),
        ('照片整理角', '相册', '照片', '拍摄日期'),
    ]
    scene, subject, item, detail = scenes[int(sha256(seed.encode()).hexdigest(), 16) % len(scenes)]
    marker = '新建的' + scene
    bodies = [
        f'这次我新建了一个独立的{scene}，和以前聊过的地方不同。我打算每周六上午在这里做{subject}记录。',
        f'这个新地方的窗边放着白色收纳盒，我把本次记录用的铅笔放在盒子里。',
        f'这次我准备用一本蓝色笔记本记录{subject}的变化。',
        f'我想先给这次的每个{item}编一个编号，方便以后查找。',
        f'记录时我会特别留意{detail}，把看到的细节写下来。',
        '我打算每次先写日期，再写当天观察到的事情。',
        '我准备在笔记本右边留出空白，方便以后补充。',
        '这次我想用绿色标签标出还需要处理的部分。',
        '完成的部分我会换成黄色标签，以免弄混。',
        '我习惯先把桌面擦干净，再开始记录。',
        '为了看清细节，我在这个地方准备了一盏小台灯。',
        '这盏台灯是可以调节角度的，光线会朝着桌面。',
        '我打算把不用的包装纸留在抽屉里，以后做草稿。',
        '抽屉左侧放常用工具，右侧留给备用材料。',
        '我想把每次使用过的工具都放回原来的位置。',
        '这次我准备了一个小托盘，专门放正在处理的东西。',
        '暂时没有处理完的东西，我会在托盘旁留一张便条。',
        '便条上我会写下一次从哪里继续，免得忘记。',
        '我希望每次记录之后都拍一张照片留念。',
        '这些照片我会放在手机里一个单独的相簿中。',
        '相簿里的照片我打算按照拍摄日期排列。',
        '月底我想挑出最有变化的一张照片，贴进笔记本。',
        '我准备用纸袋收好多余的标签，放在桌子下面。',
        '这个角落的椅子上，我想放一个柔软的坐垫。',
        '如果早上阳光太强，我会先拉上一半窗帘。',
        '我想在这个角落放一个小闹钟，提醒自己起来走走。',
        '休息时我一般会到客厅喝一杯温水。',
        '这次我给自己定的目标是慢慢完成，不赶进度。',
        '遇到拿不准的地方，我会先在笔记上画一个问号。',
        '周日晚上我打算回看这些问号，决定下一步怎么做。',
        '我想在每个月的最后一页写一句自己的感受。',
        '收拾好以后，我会把笔记本合上，放回窗边的书架。',
    ]
    count = 2 if profile == 'short' else 32
    keywords = ['蓝色', '编号', detail, '日期', '空白', '绿色', '黄色', '桌面', '台灯', '角度', '包装纸', '备用材料', '原来的位置', '小托盘', '便条', '下一次', '照片', '相簿', '拍摄日期', '月底', '纸袋', '坐垫', '窗帘', '小闹钟', '温水', '不赶进度', '问号', '周日', '感受', '书架']
    turns = []
    for i, body in enumerate(bodies[:count], 1):
        # Concrete terms from the actual utterance; no unrelated required word.
        terms = ([scene, '周六'] if i == 1 else ['白色', '铅笔'] if i == 2 else [keywords[i-3]])
        turns.append(dict(ordinal=i, text=body+'请简短回应。', requiredASRTerms=terms,
                          factKey=f'fact-{i:03d}', expectedStatement=body,
                          expectedAction='new-or-merge-theme', targetFactKey=None))
    return dict(schema='natural-conversation-v1', seed=seed, profile=profile, marker=marker,
                scenario=scene, semanticValidation='NOT_RUN', turns=turns,
                requiredMemoryTerms=[scene, '周六', '白色', '铅笔'])


def expected_for_completed(case, completed):
    if isinstance(completed, bool) or not isinstance(completed, int) or not 0 <= completed <= len(case['turns']):
        raise ValueError('invalid completed turn count')
    return dict(semanticValidation='NOT_RUN', completedTurns=completed,
                explanation='Checklist only. Compare actual transcription, candidates and formal versions; generation is not PASS.',
                facts=[{k:t[k] for k in ('ordinal','factKey','expectedStatement','expectedAction','targetFactKey')}
                       for t in case['turns'][:completed]])


def semantic_case(seed):
    rows = [
        ('我把剪刀放在白色盒子里。', 'add', None),
        ('我把剪刀放在白色盒子里。', 'duplicate-or-add-evidence', 'fact-001'),
        ('剪刀收在那个白色的盒子里。', 'semantic-repeat', 'fact-001'),
        ('这个放剪刀的白色盒子在书桌左边。', 'supplement', 'fact-001'),
        ('刚才颜色说错了，放剪刀的盒子其实是蓝色的。', 'explicit-correction', 'fact-001'),
        ('我从来不用盒子放剪刀。', 'contradiction-review-required', 'fact-001'),
    ]
    return dict(schema='semantic-special-cases-v1', seed=seed, semanticValidation='NOT_RUN',
                execution='Separate targeted test only; cannot substitute for natural short receipt or trigger automatic confirmation.',
                turns=[dict(ordinal=i,text=s,factKey=f'fact-{i:03d}',expectedStatement=s,
                            expectedAction=a,targetFactKey=t) for i,(s,a,t) in enumerate(rows,1)])
