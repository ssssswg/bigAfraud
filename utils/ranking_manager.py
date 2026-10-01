import sqlite3
import logging
import json
from datetime import datetime
from typing import List, Dict, Optional
from utils.selection_record_manager import SelectionRecordManager
from trading.stock_score_calculator import StockScoreCalculator
from utils.akshare_fetcher import AKShareFetcher
from utils.db_manager import DBManager

logger = logging.getLogger(__name__)


class RankingManager:
    """排名管理类"""
    
    def __init__(self, db_path: str = None):
        """初始化排名管理器
        
        Args:
            db_path: 数据库路径（已废弃，使用全局数据库管理器）
        """
        # 使用全局数据库管理器实例
        from utils.global_db import get_global_db
        self.db_manager = get_global_db()
        self.selection_manager = SelectionRecordManager()
        self.score_calculator = StockScoreCalculator(db_manager=self.db_manager)
        self.akshare_fetcher = AKShareFetcher()
    
    def get_available_dates(self) -> List[str]:
        """获取可用的选股日期
        
        Returns:
            可用选股日期列表，格式为YYYY-MM-DD
        """
        try:
            # 使用全局 DBManager 实例查询所有不同的选股日期
            sql = """
                SELECT DISTINCT selection_date 
                FROM stock_selection_record 
                WHERE is_active = 1 
                ORDER BY selection_date DESC
            """
            results = self.db_manager.query(sql)
            dates = [row['selection_date'] for row in results]
            return dates
        except Exception as e:
            logger.error(f"获取可用日期失败: {str(e)}")
            return []
    
    def generate_ranking(self, selection_date: str) -> List[Dict]:
        """生成指定日期的选股排名
        
        Args:
            selection_date: 选股日期，格式为YYYY-MM-DD
            
        Returns:
            排名结果列表
        """
        try:
            # 1. 查询正向策略的选股记录（排除M头和多死叉），选择没有评分或评分为0的记录，跳过-100分的记录
            sql = """
                SELECT id, stock_code, stock_name, industry, sector, selection_price, score, strategy_name
                FROM stock_selection_record 
                WHERE selection_date = ? AND is_active = 1 
                AND strategy_name NOT LIKE '%M头%' 
                AND strategy_name NOT LIKE '%多死叉%'
                AND (score IS NULL OR score = 0.0)
            """
            records = self.db_manager.query(sql, (selection_date,))
            
            if not records:
                # 如果没有需要评分的股票，返回现有的排名结果
                logger.info(f"日期 {selection_date} 没有需要生成排名的股票（所有股票都已有评分），返回现有排名")
                return self._get_existing_ranking(selection_date)
            
            # 2. 批量获取有效股票代码（有策略名称的）
            valid_records = [r for r in records if r.get('strategy_name')]
            if not valid_records:
                logger.info(f"日期 {selection_date} 没有有效股票（都缺少策略名称）")
                return self._get_existing_ranking(selection_date)
            
            # 3. 批量查询股票的策略命中数（一次查询，避免N+1问题）
            stock_codes = [r['stock_code'] for r in valid_records]
            strategy_counts = self._batch_get_strategy_counts(stock_codes, selection_date)
            
            # 4. 使用批量评分方法并行计算
            valid_stock_codes = [
                r['stock_code'] for r in valid_records 
                if strategy_counts.get(r['stock_code'], 0) > 0
            ]
            
            if not valid_stock_codes:
                logger.info(f"日期 {selection_date} 没有命中策略的股票")
                return self._get_existing_ranking(selection_date)
            
            # 使用批量评分（合并后的方法支持单只和批量）
            logger.info(f"开始批量评分 {len(valid_stock_codes)} 只股票")
            score_results = self.score_calculator.calculate_score(valid_stock_codes, selection_date)
            
            # 保存评分详情到数据库（用于获取板块信息）
            try:
                from trading.stock_score_dao import StockScoreDAO
                score_dao = StockScoreDAO()
                saved_count = score_dao.save_batch_scores(score_results)
                logger.info(f"批量保存评分详情成功: {saved_count}/{len(score_results)}")
            except Exception as save_error:
                logger.warning(f"批量保存评分详情失败: {save_error}")
            
            # 构建股票代码到评分的映射
            score_map = {r.stock_code: r for r in score_results}
            
            # 5. 构建排名结果
            ranking_results = []
            for record in valid_records:
                stock_code = record['stock_code']
                if strategy_counts.get(stock_code, 0) == 0:
                    continue
                
                score_result = score_map.get(stock_code)
                if not score_result:
                    continue
                
                ranking_results.append({
                    'id': record['id'],
                    'stock_code': stock_code,
                    'stock_name': record['stock_name'],
                    'industry': record['industry'],
                    'sector': record['sector'],
                    'selection_price': record['selection_price'],
                    'score': score_result.total_score
                })
            
            # 6. 按评分降序排序
            ranking_results.sort(key=lambda x: x['score'], reverse=True)
            
            # 7. 批量更新数据库（使用事务和批量更新）
            self._batch_update_ranking(ranking_results, selection_date)
            
            # 8. 返回最终排名结果
            return self._get_existing_ranking(selection_date)
            
        except Exception as e:
            logger.error(f"生成排名失败: {str(e)}")
            return []
    
    def _get_existing_ranking(self, selection_date: str) -> List[Dict]:
        """获取已有的排名结果

        卖点持久化在 stock_selection_record 的 sell_status/sell_price/sell_reason/sell_yield：
        - 已卖出的记录（sell_status='卖出'）直接采用库中固化的卖点，不再重新生成/重算
        - 持有/无卖点的记录实时计算；若新触发卖出，写库固化，之后不再重算
        """
        existing_sql = """
            SELECT id, stock_code, stock_name, industry, sector, selection_price, score, rank_position,
                   sell_status, sell_price, sell_reason, sell_yield
            FROM stock_selection_record 
            WHERE selection_date = ? AND is_active = 1 
            AND strategy_name NOT LIKE '%M头%' 
            AND strategy_name NOT LIKE '%多死叉%'
            AND score > 0.0
            ORDER BY rank_position ASC
        """
        existing_records = self.db_manager.query(existing_sql, (selection_date,))

        results = []
        to_freeze = []  # 新触发卖出需写库固化的记录
        for record in existing_records:
            # 已卖出的票：直接采用库中固化的卖点，不再重新生成
            if record.get('sell_status') == '卖出':
                results.append({
                    'id': record['id'],
                    'stock_code': record['stock_code'],
                    'stock_name': record['stock_name'],
                    'industry': record['industry'],
                    'sector': record['sector'],
                    'selection_price': record['selection_price'],
                    'score': record['score'],
                    'rank_position': record['rank_position'],
                    'sell_price': record['sell_price'],
                    'sell_reason': record['sell_reason'] or '',
                    'sell_yield': record['sell_yield'],
                    'sell_status': '卖出',
                })
                continue

            # 持有/无卖点：实时计算
            sell = self._compute_sell_advice(
                record['stock_code'], record['selection_price'], selection_date
            )
            sell_status = sell['sell_status'] if sell else '持有'
            sell_price = sell['sell_price'] if sell else None
            sell_reason = sell['sell_reason'] if sell else ''
            sell_yield = sell['sell_yield'] if sell else None

            # 新触发卖出：写库固化，后续不再重算
            if sell and sell_status == '卖出':
                to_freeze.append((sell_status, sell_price, sell_reason, sell_yield, record['id']))

            results.append({
                'id': record['id'],
                'stock_code': record['stock_code'],
                'stock_name': record['stock_name'],
                'industry': record['industry'],
                'sector': record['sector'],
                'selection_price': record['selection_price'],
                'score': record['score'],
                'rank_position': record['rank_position'],
                'sell_price': sell_price,
                'sell_reason': sell_reason,
                'sell_yield': sell_yield,
                'sell_status': sell_status,
            })

        # 固化新触发卖出的记录（写库，保证后续重新生成不再重算）
        if to_freeze:
            try:
                freeze_sql = "UPDATE stock_selection_record SET sell_status=?, sell_price=?, sell_reason=?, sell_yield=? WHERE id=?"
                for row in to_freeze:
                    self.db_manager.execute_with_retry(freeze_sql, row)
            except Exception as e:
                logger.warning(f"固化卖出记录失败: {e}")

        return results
    
    def _batch_get_strategy_counts(self, stock_codes: List[str], selection_date: str) -> Dict[str, int]:
        """批量查询股票的策略命中数"""
        if not stock_codes:
            return {}
        
        # 使用IN查询批量获取
        placeholders = ','.join('?' * len(stock_codes))
        sql = f"""
            SELECT stock_code, COUNT(*) as count 
            FROM stock_selection_record 
            WHERE stock_code IN ({placeholders}) AND selection_date = ? AND is_active = 1
            GROUP BY stock_code
        """
        
        try:
            results = self.db_manager.query(sql, tuple(stock_codes) + (selection_date,))
            return {r['stock_code']: r['count'] for r in results}
        except Exception as e:
            logger.error(f"批量查询策略计数失败: {e}")
            return {}
    
    def _batch_update_ranking(self, ranking_results: List[Dict], selection_date: str):
        """批量更新排名到数据库"""
        if not ranking_results:
            return
        
        conn = None
        try:
            # 独占写窗口：选股排名批量写与K线更新/财务入库等其它写错峰，避免 SQLite 库级锁互撞
            self.db_manager.begin_bulk_write()
            conn = self.db_manager.connect()
            cursor = conn.cursor()
            
            # 批量更新评分和排名
            update_sql = """
                UPDATE stock_selection_record 
                SET score = ?, rank_position = ?, sector = ? 
                WHERE id = ?
            """
            
            for i, result in enumerate(ranking_results, 1):
                sector = self._get_best_sector(result['stock_code'], selection_date)
                result['sector'] = sector
                result['rank_position'] = i
                
                cursor.execute(update_sql, (result['score'], i, sector, result['id']))
            
            # 重新生成完整排名（包含已有评分的股票）
            all_stocks_sql = """
                SELECT id, stock_code, score 
                FROM stock_selection_record 
                WHERE selection_date = ? AND is_active = 1 
                AND strategy_name NOT LIKE '%M头%' 
                AND strategy_name NOT LIKE '%多死叉%'
                AND score > 0.0
                ORDER BY score DESC
            """
            cursor.execute(all_stocks_sql, (selection_date,))
            all_stocks = cursor.fetchall()
            
            update_rank_sql = """
                UPDATE stock_selection_record 
                SET rank_position = ? 
                WHERE id = ?
            """
            for i, stock in enumerate(all_stocks, 1):
                cursor.execute(update_rank_sql, (i, stock['id']))
            
            conn.commit()
            logger.info(f"批量更新排名成功，共 {len(ranking_results)} 只新股票，总排名 {len(all_stocks)} 只")
            
        except Exception as e:
            logger.error(f"批量更新排名失败: {e}")
            if conn:
                conn.rollback()
        finally:
            self.db_manager.end_bulk_write()
            if conn:
                conn.close()
    
    def _calculate_score(self, stock_code: str, selection_date: str) -> float:
        """计算股票综合评分
        
        Args:
            stock_code: 股票代码
            selection_date: 选股日期
            
        Returns:
            综合评分值
        """
        try:
            # 调用综合评分计算器
            score_result = self.score_calculator.calculate_score(stock_code, selection_date)
            
            # 保存详细评分信息到stock_score_detail表
            try:
                from trading.stock_score_dao import StockScoreDAO
                score_dao = StockScoreDAO()
                score_dao.save_score(score_result)
                logger.debug(f"保存评分详情成功: {stock_code} {selection_date}")
            except Exception as save_error:
                logger.warning(f"保存评分详情失败 {stock_code}: {save_error}")
            
            # 返回综合评分
            return score_result.total_score
        except Exception as e:
            logger.warning(f"计算综合评分失败 {stock_code}: {str(e)}")
            return 0.0
    
    def _get_best_sector(self, stock_code: str, score_date: str) -> str:
        """获取股票得分最高的板块信息
        
        Args:
            stock_code: 股票代码
            score_date: 评分日期，用于获取对应日期的板块信息
            
        Returns:
            板块名称，如果获取失败返回空字符串
        """
        try:
            # 从stock_score_detail表获取指定日期的板块详情
            sql = "SELECT sector_details FROM stock_score_detail WHERE stock_code = ? AND score_date = ? LIMIT 1"
            logger.debug(f"查询板块信息: 股票={stock_code}, 日期={score_date}")
            
            conn = self.db_manager.connect()
            cursor = conn.cursor()
            cursor.execute(sql, (stock_code, score_date))
            rows = cursor.fetchall()
            
            logger.debug(f"查询结果: {len(rows)}条记录")
            if rows:
                sector_details = rows[0][0] if rows[0] else None
                logger.debug(f"板块详情: {sector_details}")
                
                if sector_details:
                    try:
                        import json
                        # 解析JSON格式的板块详情
                        sector_data = json.loads(sector_details)
                        
                        # 从sector_details中获取板块名称
                        if 'sector_name' in sector_data and sector_data['sector_name']:
                            logger.debug(f"获取板块名称成功: {sector_data['sector_name']}")
                            return sector_data['sector_name']
                        else:
                            logger.debug(f"板块详情中没有sector_name字段: {sector_data}")
                    except Exception as e:
                        logger.debug(f"解析板块详情失败: {str(e)}")
            else:
                logger.debug(f"没有找到板块详情记录")
            
            # 板块评分未产出时，用 stock_basic.industry 兜底，避免板块显示"未知"
            try:
                ind_rows = self.db_manager.query(
                    "SELECT industry FROM stock_basic WHERE code = ?", (stock_code,))
                if ind_rows and ind_rows[0] and ind_rows[0]['industry']:
                    logger.debug(f"板块评分未产出，用行业兜底: {stock_code} -> {ind_rows[0]['industry']}")
                    return ind_rows[0]['industry']
            except Exception as ind_e:
                logger.debug(f"行业兜底失败 {stock_code}: {ind_e}")

            logger.debug(f"无法获取股票 {stock_code} 的板块信息")
            return ''
        except Exception as e:
            logger.warning(f"获取股票 {stock_code} 板块信息失败: {str(e)}")
            return ''
    
    def _compute_sell_advice(self, stock_code: str, selection_price: float, selection_date: str) -> Optional[Dict]:
        """逆人性卖出信号（转发到独立函数 compute_sell_signal，供选股持有决策历史回扫复用）"""
        from utils.sell_signal import compute_sell_signal
        return compute_sell_signal(self.db_manager, stock_code, selection_price, selection_date)

    def track_ranking(self, selection_date: str, top_n: int = 5, sort_by: str = 'yield') -> List[Dict]:
        """跟踪指定日期的排名

        Args:
            selection_date: 选股日期，格式为YYYY-MM-DD
            top_n: 返回前N条记录
            sort_by: 排序字段，'yield'（默认，按收益率降序）或 'strategy'（按入选策略名称，相同策略排一起）

        Returns:
            排名跟踪结果列表
        """
        try:
            # 1. 获取指定日期的全部有效记录（含策略名与关键日期说明）
            sql = """
                SELECT id, stock_code, stock_name, industry, sector, selection_price, score, rank_position,
                       strategy_name, key_dates
                FROM stock_selection_record 
                WHERE selection_date = ? AND is_active = 1 
                AND score IS NOT NULL 
                ORDER BY score DESC
            """
            records = self.db_manager.query(sql, (selection_date,))
            if not records:
                return []

            # 评分排名：该日全部记录按 score 降序的名次（同分同名次）
            score_sorted = sorted(records, key=lambda r: r['score'] or 0, reverse=True)
            score_rank_map = {}
            prev = None
            cur_rank = 0
            for r in score_sorted:
                s = r['score'] or 0
                if s != prev:
                    cur_rank += 1
                    prev = s
                score_rank_map[r['stock_code']] = cur_rank

            # 2. 计算实时数据
            tracking_results = []
            for record in records:
                stock_code = record['stock_code']
                stock_name = record['stock_name']
                industry = record['industry']
                sector = record['sector']
                selection_price = record['selection_price']
                score = record['score']

                # 获取实时价格
                current_price = self.akshare_fetcher.get_stock_price(stock_code)

                # 计算当前收益率
                current_yield = 0.0
                if current_price and selection_price:
                    current_yield = (current_price - selection_price) / selection_price * 100

                # 获取选入后最高价格
                highest_price = self._get_highest_price(stock_code, selection_date)

                # 计算最高收益率
                highest_yield = 0.0
                if highest_price and selection_price:
                    highest_yield = (highest_price - selection_price) / selection_price * 100

                tracking_results.append({
                    'rank_position': 0,  # 收益率排序后重编号
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'score': score,
                    'score_rank': score_rank_map.get(stock_code, 0),
                    'industry': industry,
                    'sector': sector,
                    'selection_price': selection_price,
                    'current_price': current_price,
                    'current_yield': round(current_yield, 2),
                    'highest_price': highest_price,
                    'highest_yield': round(highest_yield, 2),
                    'strategies': self._format_strategies(record.get('strategy_name'), record.get('key_dates'))
                })

            # 3. 排序：默认按收益率降序；可选按入选策略名称（相同策略排一起，组内收益率降序；空策略排最后）
            if sort_by == 'strategy':
                tracking_results.sort(key=lambda x: (x['strategies'] or '\U0010FFFF', -x['current_yield']))
            else:
                tracking_results.sort(key=lambda x: x['current_yield'], reverse=True)
            for i, item in enumerate(tracking_results, 1):
                item['rank_position'] = i

            return tracking_results[:top_n]
        except Exception as e:
            logger.error(f"跟踪排名失败: {str(e)}")
            return []

    def _format_strategies(self, strategy_name: Optional[str], key_dates: Optional[str]) -> str:
        """格式化入选策略及关键日期说明"""
        parts = []
        if strategy_name:
            parts.append(strategy_name)
        if key_dates:
            try:
                kd = json.loads(key_dates) if isinstance(key_dates, str) else key_dates
                if isinstance(kd, list):
                    for item in kd:
                        if not isinstance(item, dict):
                            continue
                        desc = item.get('type') or item.get('description') or ''
                        d = (item.get('date') or '')[:10]
                        seg = f"{desc} {d}".strip()
                        if seg:
                            parts.append(seg)
            except Exception:
                pass
        return '；'.join(parts) if parts else ''

    def _get_highest_price(self, stock_code: str, selection_date: str) -> float:
        """获取选入后的最高价格
        
        Args:
            stock_code: 股票代码
            selection_date: 选股日期
        
        Returns:
            最高价格，遵循以下规则：
            1. 当当前日为选入日时，最高价和选入价格、收盘价相同
            2. 选取选入后出现的最高价格，包括当天的实时数据
            3. 没有任何k线数据和实时数据时，和当前价格，选入价格一致
        """
        try:
            from utils.db_manager import DBManager
            import pandas as pd
            from datetime import datetime, timedelta
            
            logger.debug(f"计算 {stock_code} 从 {selection_date} 开始的最高价")
            
            # 获取当前日期
            today = datetime.now().strftime('%Y-%m-%d')
            logger.debug(f"当前日期: {today}")
            
            # 检查当前日期是否为选入日期
            if today == selection_date:
                logger.debug(f"当前日为选入日，返回当前价格")
                # 当当前日为选入日时，最高价和选入价格、收盘价相同
                current_price = self.akshare_fetcher.get_stock_price(stock_code)
                if current_price is not None:
                    return current_price
                # 如果当前价格获取失败，返回0.0
                return 0.0
            
            # 使用全局数据库管理器实例
            from utils.global_db import get_global_db
            db_manager = get_global_db()
            
            # 从数据库读取股票数据
            df = db_manager.read_stock(stock_code)
            
            # 获取当前价格
            current_price = self.akshare_fetcher.get_stock_price(stock_code)
            logger.debug(f"当前价格: {current_price}")
            
            # 初始化最高价格为当前价格
            highest_price = current_price
            
            # 转换选股日期为datetime格式
            selection_date_dt = pd.to_datetime(selection_date)
            # 选入日期的下一天开始
            next_day = selection_date_dt + timedelta(days=1)
            logger.debug(f"选入日期: {selection_date_dt}, 下一天: {next_day}")
            
            # 如果有K线数据，查询选入日期后的最高价格
            if df is not None and not df.empty:
                # 转换日期格式并按日期升序排列
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date').reset_index(drop=True)
                
                # 筛选选入日期之后的数据（不包括选入日期当天）
                after_selection = df[df['date'] >= next_day]
                logger.debug(f"筛选后的数据条数: {len(after_selection)}")
                
                if not after_selection.empty:
                    # 使用选入日期到今天的K线数据中的最高价
                    kline_highest = after_selection['high'].max()
                    logger.debug(f"K线最高价: {kline_highest}")
                    # 取K线最高价和当前价格的最大值
                    if kline_highest > highest_price:
                        highest_price = kline_highest
            
            # 尝试从腾讯财经获取今天的实时最高价
            try:
                # 构建腾讯财经查询代码
                if stock_code.startswith('6') or stock_code.startswith('8'):
                    query_code = f"sh{stock_code}"
                else:
                    query_code = f"sz{stock_code}"
                
                # 调用腾讯财经接口获取实时数据
                import requests
                url = f"https://qt.gtimg.cn/q={query_code}"
                resp = requests.get(url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                
                # 设置正确的字符编码
                resp.encoding = 'gbk'
                
                if resp.status_code == 200:
                    text = resp.text.strip()
                    if '~' in text:
                        parts = text.split('~')
                        # 腾讯接口格式: parts[33]=最高价
                        if len(parts) >= 35:
                            try:
                                today_high = float(parts[33]) if parts[33] else highest_price
                                logger.debug(f"腾讯财经最高价: {today_high}")
                                # 与当前最高价格比较，取最大值
                                if today_high > highest_price:
                                    highest_price = today_high
                            except (ValueError, IndexError) as e:
                                logger.debug(f"解析腾讯财经数据失败: {e}")
            except Exception as e:
                logger.debug(f"获取今天的最高最低价失败: {str(e)}")
            
            logger.debug(f"最终最高价: {highest_price}")
            
            # 如果没有任何数据，返回0.0
            if highest_price is None:
                return 0.0
            
            return highest_price
        except Exception as e:
            logger.warning(f"获取最高价格失败 {stock_code}: {str(e)}")
            # 出错时返回当前价格
            current_price = self.akshare_fetcher.get_stock_price(stock_code)
            if current_price is not None:
                return current_price
            # 如果当前价格也获取失败，返回0.0
            return 0.0

    def regenerate_ranking(self, selection_date: str, force_recalculate: bool = False) -> Dict:
        """重新生成指定日期的排名
        
        用于修复评分不完整或为0的情况。可以选择是否强制重新计算所有评分。
        
        Args:
            selection_date: 选股日期，格式为YYYY-MM-DD
            force_recalculate: 是否强制重新计算所有评分（默认False，只重新计算评分为0的股票）
        
        Returns:
            {'success': True/False, 'message': '...', 'total': 10, 'recalculated': 5, 'failed': 0}
        """
        try:
            logger.info(f"开始重新生成排名: {selection_date}, 强制重新计算: {force_recalculate}")
            
            # 0. 清除旧的评分数据（强制重新计算时）
            if force_recalculate:
                try:
                    # 先删子表 detail（外键引用 score），再删父表 score，避免 FOREIGN KEY 约束失败
                    delete_detail_sql = "DELETE FROM stock_score_detail WHERE score_date = ?"
                    self.db_manager.execute_with_retry(delete_detail_sql, (selection_date,))
                    logger.info(f"已清除 {selection_date} 的旧评分详情数据")

                    delete_score_sql = "DELETE FROM stock_score WHERE score_date = ?"
                    self.db_manager.execute_with_retry(delete_score_sql, (selection_date,))
                    logger.info(f"已清除 {selection_date} 的旧评分数据")
                    
                    # 提交删除操作
                    conn = self.db_manager.connect()
                    conn.commit()
                except Exception as e:
                    logger.warning(f"清除旧数据失败: {e}")
            
            # 1. 查询需要重新计算的股票
            if force_recalculate:
                # 强制重新计算所有股票
                sql = """
                    SELECT id, stock_code, stock_name, industry, sector, selection_price, strategy_name
                    FROM stock_selection_record 
                    WHERE selection_date = ? AND is_active = 1 
                    AND strategy_name NOT LIKE '%M头%' 
                    AND strategy_name NOT LIKE '%多死叉%'
                """
                logger.info(f"强制重新计算所有股票的评分")
            else:
                # 只重新计算评分为0或NULL的股票
                sql = """
                    SELECT id, stock_code, stock_name, industry, sector, selection_price, strategy_name
                    FROM stock_selection_record 
                    WHERE selection_date = ? AND is_active = 1 
                    AND strategy_name NOT LIKE '%M头%' 
                    AND strategy_name NOT LIKE '%多死叉%'
                    AND (score IS NULL OR score = 0.0)
                """
                logger.info(f"重新计算评分为0或NULL的股票")
            
            records = self.db_manager.query(sql, (selection_date,))
            
            if not records:
                logger.info(f"日期 {selection_date} 没有需要重新计算的股票")
                return {
                    'success': True,
                    'message': f'没有需要重新计算的股票',
                    'total': 0,
                    'recalculated': 0,
                    'failed': 0
                }
            
            # 2. 重新计算每只股票的评分
            recalculated_count = 0
            failed_count = 0
            
            for record in records:
                record_id = record['id']
                stock_code = record['stock_code']
                stock_name = record['stock_name']
                
                try:
                    # 重新计算评分
                    score = self._calculate_score(stock_code, selection_date)
                    logger.info(f"重新计算评分: {stock_code}({stock_name}) = {score}")
                    
                    # 获取最佳板块
                    sector = self._get_best_sector(stock_code, selection_date)
                    
                    # 更新数据库
                    update_sql = """
                        UPDATE stock_selection_record 
                        SET score = ?, sector = ? 
                        WHERE id = ?
                    """
                    cursor = self.db_manager.execute_with_retry(update_sql, (score, sector, record_id))
                    logger.debug(f"更新评分成功: ID={record_id}, 股票={stock_code}, 评分={score}, 板块={sector}")
                    recalculated_count += 1
                    
                except Exception as e:
                    logger.error(f"重新计算评分失败: {stock_code}({stock_name}) - {str(e)}")
                    failed_count += 1
            
            # 3. 提交事务
            try:
                conn = self.db_manager.connect()
                conn.commit()
                logger.debug("评分更新事务提交成功")
            except Exception as e:
                logger.error(f"事务提交失败: {e}")
            
            # 4. 重新生成排名
            try:
                # 查询所有有评分的股票
                all_stocks_sql = """
                    SELECT id, stock_code, stock_name, industry, sector, selection_price, score
                    FROM stock_selection_record 
                    WHERE selection_date = ? AND is_active = 1 
                    AND strategy_name NOT LIKE '%M头%' 
                    AND strategy_name NOT LIKE '%多死叉%'
                    AND score > 0.0
                    ORDER BY score DESC
                """
                all_stocks = self.db_manager.query(all_stocks_sql, (selection_date,))
                
                # 重新分配排名
                for i, stock in enumerate(all_stocks, 1):
                    update_rank_sql = """
                        UPDATE stock_selection_record 
                        SET rank_position = ? 
                        WHERE id = ?
                    """
                    try:
                        self.db_manager.execute_with_retry(update_rank_sql, (i, stock['id']))
                    except Exception as e:
                        logger.error(f"更新排名失败: {stock['id']} - {e}")
                
                # 提交排名更新
                conn = self.db_manager.connect()
                conn.commit()
                logger.info(f"已重新生成排名，共 {len(all_stocks)} 只股票")
                
            except Exception as e:
                logger.error(f"重新生成排名失败: {str(e)}")
            
            # 5. 返回结果
            result = {
                'success': True,
                'message': f'重新生成排名完成: 重新计算 {recalculated_count} 只股票，失败 {failed_count} 只',
                'total': len(records),
                'recalculated': recalculated_count,
                'failed': failed_count
            }
            
            logger.info(f"重新生成排名完成: {result}")
            return result
            
        except Exception as e:
            logger.error(f"重新生成排名失败: {str(e)}")
            return {
                'success': False,
                'message': f'重新生成排名失败: {str(e)}',
                'total': 0,
                'recalculated': 0,
                'failed': 0
            }
