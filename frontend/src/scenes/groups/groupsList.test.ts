import { router } from 'kea-router'
import { expectLogic } from 'kea-test-utils'
import { defaultAPIMocks, mockAPI, MOCK_TEAM_ID } from 'lib/api.mock'
import { urls } from 'scenes/urls'
import { initKeaTestLogic } from '~/test/init'
import { groupsListLogic } from './groupsListLogic'

jest.mock('lib/api')

describe('groupsListLogic', () => {
    let logic: ReturnType<typeof groupsListLogic.build>

    mockAPI(async (url) => {
        const { pathname } = url
        if (`api/projects/${MOCK_TEAM_ID}/groups/?group_type_index=0` === pathname) {
            return { result: ['result from api'], next: null, previous: null }
        }
        return defaultAPIMocks(url)
    })

    initKeaTestLogic({
        logic: groupsListLogic,
        props: {},
        onLogic: (l) => (logic = l),
    })

    beforeEach(() => {
        jest.spyOn(logic.selectors, 'groupsEnabled').mockReturnValue(true)
    })

    it('sets the tab and loads groups upon tab change', async () => {
        router.actions.push(urls.groups('0'))
        await expectLogic(logic)
            .toDispatchActions(['setTab'])
            .toMatchValues({ currentTab: '0' })
            .toDispatchActions(['loadGroups'])
    })
})
