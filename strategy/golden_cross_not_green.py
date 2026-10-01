"""
金叉不绿策略 - MACD水上金叉且绿柱数量≤3根
"""
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategy.base_strategy import BaseStrategy


class GoldenCrossNotGreenStrategy(BaseStrategy):
    """金叉不绿策略"""

    def __init__(self, params=None):
        default_params = {
            'macd_fast': 12,           # MACD短期EMA周期
            'macd_slow': 26,           # MACD长期EMA周期
            'macd_signal': 9,          # MACD信号线周期
            'max_green_bars': 3,       # 金叉前允许的最大绿柱数量
            'dif_above_zero': True,    # DIF是否需要在零轴上方
            'dea_above_zero': True,    # DEA是否需要在零轴上方
            'bollinger_period': 20,    # 布林带周期
            'bollinger_std': 2,        # 布林带标准差倍数
            'bollinger_expanding': True,  # 是否要求布林带张口扩大
        }
        if params:
            default_params.update(params)
        super().__init__("金叉不绿策略", default_params)

    def calculate_indicators(self, df) -> pd.DataFrame:
        """计算MACD和布林带技术指标"""
        result = df.copy()

        # 检查数据是否为倒序排列，如果是则反转
        if len(result) > 1 and str(result['date'].iloc[0]) > str(result['date'].iloc[1]):
            result = result.iloc[::-1].reset_index(drop=True)

        # 计算MACD指标
        macd_fast = self.params.get('macd_fast', 12)
        macd_slow = self.params.get('macd_slow', 26)
        macd_signal = self.params.get('macd_signal', 9)

        # 计算MACD指标（官方优先）
        from utils import technical as _tech
        _macd = _tech.MACD(result, macd_fast, macd_slow, macd_signal)
        result['dif'] = _macd['macd']
        result['dea'] = _macd['macd_signal']
        result['macd'] = _macd['macd'] - _macd['macd_signal']  # 原语义：dif-dea（1×）

        # 计算布林带指标（官方优先）
        bollinger_period = self.params.get('bollinger_period', 20)
        bollinger_std = self.params.get('bollinger_std', 2)
        _boll = _tech.Bollinger(result, bollinger_period, bollinger_std)
        result['boll_mid'] = _boll['bb_mid']
        result['boll_upper'] = _boll['bb_upper']
        result['boll_lower'] = _boll['bb_lower']
        # 布林带宽度（上轨 - 下轨）
        result['boll_width'] = result['boll_upper'] - result['boll_lower']

        # 填充缺失值
        # 注意：只使用bfill()向后填充，避免引入未来函数
        # ffill()会使用未来数据填充NaN，导致回测结果失真
        # result = result.ffill().bfill()  # ⚠️ ffill是未来函数！
        result = result.bfill()

        # 反转回倒序
        result = result.iloc[::-1].reset_index(drop=True)
        return result

    def get_selection_criteria(self):
        """获取选股条件描述"""
        return [
            "1. DIF > 0（零轴上方）",
            "2. DEA > 0（零轴上方）",
            "3. DIF上穿DEA（金叉）",
            "4. 金叉前绿柱数量 ≤ 3",
            "5. 布林带张口扩大（当周宽度 > 前周宽度）",
        ]

    def select_stocks(self, df, stock_name='', stock_code='') -> list:
        """选股逻辑"""
        if df.empty or len(df) < 30:
            return []

        # 快速预检查：过滤ST和退市股票
        if stock_name:
            if not self._validate_stock_name(stock_name):
                return []

        # 获取参数
        max_green_bars = self.params.get('max_green_bars', 3)
        dif_above_zero = self.params.get('dif_above_zero', True)
        dea_above_zero = self.params.get('dea_above_zero', True)
        bollinger_expanding = self.params.get('bollinger_expanding', True)

        # 获取最新数据和前一天数据
        latest = df.iloc[0]  # 最新一天数据（倒序，最新在前）
        prev = df.iloc[1]     # 前一天数据

        # 检查指标是否有效
        if pd.isna(latest['dif']) or pd.isna(latest['dea']):
            return []

        # 零轴判断（检查前一天的数据）
        dif_above = prev['dif'] > 0 if dif_above_zero else True
        dea_above = prev['dea'] > 0 if dea_above_zero else True

        # 金叉判断：当前DIF > DEA*1.2（要求DIF明显上穿DEA），且前一日DIF <= DEA
        golden_cross = (latest['dif'] > latest['dea'] * 1.2) and (prev['dif'] <= prev['dea'])

        # 统计金叉前连续绿柱数量
        green_bars_count = 0
        
        for i in range(1, max_green_bars + 2):
            if i < len(df) and df['macd'].iloc[i] < 0:
                green_bars_count += 1
            else:
                break

        # 布林带张口扩大判断：当周布林带宽度 > 前周布林带宽度
        bollinger_ok = True
        if bollinger_expanding:
            # 检查布林带宽度数据是否有效
            if pd.isna(latest.get('boll_width')) or pd.isna(prev.get('boll_width')):
                bollinger_ok = False
            else:
                bollinger_ok = latest['boll_width'] > prev['boll_width']

        # 判断是否满足所有条件
        if dif_above and dea_above and golden_cross and (green_bars_count <= max_green_bars) and bollinger_ok:
            # 计算信号强度
            signal_strength = max(0.7, 1.0 - green_bars_count * 0.1)

            return [{
                'code': stock_code,
                'name': stock_name,
                'signal': 'buy',
                'key_date': str(df.iloc[0]['date']).split()[0],
                'key_date_type': '金叉不绿',
                'price': float(latest['close']),
                'dif': float(latest['dif']),
                'dea': float(latest['dea']),
                'macd_hist': float(latest['macd']),
                'green_bars_count': green_bars_count,
                'signal_strength': signal_strength,
                'reason': 'MACD水上金叉：DIF=%s上穿DEA=%s，金叉前绿柱%s根，布林带张口扩大' % (
                    round(latest['dif'], 4), round(latest['dea'], 4), green_bars_count),
                'details': {
                    'dif': float(latest['dif']),
                    'dea': float(latest['dea']),
                    'macd_hist': float(latest['macd']),
                    'green_bars_count': green_bars_count,
                    'signal_strength': signal_strength,
                    'boll_width': float(latest.get('boll_width', 0)),
                    'boll_width_prev': float(prev.get('boll_width', 0)),
                    'bollinger_expanding': bollinger_ok,
                }
            }]

        return []